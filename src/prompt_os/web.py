from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser

from .agent_loop import open_session
from .app_pack import AppPack, discover_app_packs
from .contract_review import (
    format_contract_review,
    list_contract_candidates,
    promote_contract_candidate,
    review_contract_candidate,
)
from .eval_runner import collect_evaluation
from .improvement import (
    format_improvement_proposal,
    improve,
    improvement_is_promotable,
    list_improvement_candidates,
    load_improvement_candidate,
    promote_improvement,
)
from .model_client import LiteLLMConfig, check_model
from .store import DocumentStore
from .tool_service import database_path
from .tracing import TraceWriter, utc_now
from .view_description import view_from_tool_calls
from .workspace import archive_document, capture_document, distill_workspace


STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 1_000_000


@dataclass(frozen=True)
class WebResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"

    @classmethod
    def json(cls, payload: Any, status: int = 200) -> "WebResponse":
        return cls(
            status=status,
            body=(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n").encode(
                "utf-8"
            ),
        )

    @classmethod
    def error(cls, message: str, status: int = 400) -> "WebResponse":
        return cls.json({"error": message}, status=status)

    @classmethod
    def html(cls, body: bytes) -> "WebResponse":
        return cls(status=200, body=body, content_type="text/html; charset=utf-8")


class ApplicationChat:
    """Persistent conversation handle for one application pack."""

    def __init__(self, root: Path, pack: AppPack, *, timezone: str, debug: bool) -> None:
        config = LiteLLMConfig.from_environment()
        check_model(config)
        self.model = config.model
        self.timezone = timezone
        self.debug = debug
        self._trace = TraceWriter(pack.trace_path(root), mode=pack.trace_mode)
        self._app_id = pack.id
        self._session = open_session(
            config, root, pack, timezone=timezone, trace_path=self._trace.path
        )
        self._session.__enter__()
        self._lock = threading.Lock()
        self._closed = False

    def send(self, message: str) -> dict[str, Any]:
        started_at = utc_now()
        with self._lock:
            if self._closed:
                raise RuntimeError("session is closed")
            try:
                outcome = self._session.send(message)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                self._trace.write(
                    app_id=self._app_id,
                    model=self.model,
                    user_message=message,
                    started_at=started_at,
                    outcome="error",
                    error=detail,
                )
                raise
            self._trace.write(
                app_id=self._app_id,
                model=self.model,
                user_message=message,
                started_at=started_at,
                outcome="completed",
                reply=outcome["reply"],
                tool_calls=outcome["tool_calls"],
            )
            return outcome

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._session.__exit__(None, None, None)


class PromptWeb:
    """Generic browser client for conversation and operator actions."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._sessions: dict[str, Any] = {}
        self._session_lock = threading.Lock()

    def close(self) -> None:
        with self._session_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        body: bytes = b"",
        query: str = "",
    ) -> WebResponse:
        parsed = urlparse(path)
        route = unquote(parsed.path)
        query_string = query or parsed.query
        parts = [part for part in route.split("/") if part]
        try:
            return self._route(method.upper(), parts, body=body, query=query_string)
        except KeyError as exc:
            return WebResponse.error(str(exc) or "not found", status=404)
        except (ValueError, RuntimeError) as exc:
            return WebResponse.error(str(exc), status=400)
        except Exception as exc:
            return WebResponse.error(f"{type(exc).__name__}: {exc}", status=500)

    def _route(
        self,
        method: str,
        parts: list[str],
        *,
        body: bytes,
        query: str,
    ) -> WebResponse:
        if method == "GET" and parts == []:
            return self._index()
        if method == "GET" and parts == ["api", "runtime"]:
            return self._runtime()
        if method == "GET" and parts == ["api", "apps"]:
            return WebResponse.json({"apps": [_pack_summary(pack) for pack in self._packs()]})
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "apps":
            pack = self._pack(parts[2])
            rest = parts[3:]
            if method == "GET" and rest == []:
                return WebResponse.json(self._app_detail(pack))
            if method == "POST" and rest == ["chat"]:
                return self._chat(pack, body)
            if method == "POST" and rest == ["session", "reset"]:
                self._drop_session(pack.id)
                return WebResponse.json({"status": "reset"})
            if method == "GET" and rest == ["improvements"]:
                return WebResponse.json(
                    {"candidates": list_improvement_candidates(self.root, pack)}
                )
            if method == "GET" and len(rest) == 2 and rest[0] == "improvements":
                return WebResponse.json(
                    self._improvement_payload(
                        pack, load_improvement_candidate(self.root, pack, rest[1])
                    )
                )
            if method == "POST" and rest == ["improve"]:
                payload = _read_json(body)
                result = improve(
                    self.root,
                    pack,
                    trace_limit=int(payload.get("trace_limit", 20)),
                )
                return WebResponse.json(self._improvement_payload(pack, result))
            if (
                method == "POST"
                and len(rest) == 3
                and rest[0] == "improve"
                and rest[2] == "promote"
            ):
                result = load_improvement_candidate(self.root, pack, rest[1])
                promotion = promote_improvement(self.root, pack, result)
                self._drop_session(pack.id)
                return WebResponse.json(promotion)
            if method == "GET" and rest == ["contract"]:
                params = parse_qs(query)
                candidate_id = params.get("candidate_id", [None])[0]
                listing = list_contract_candidates(self.root, pack)
                if candidate_id is None and not listing["candidates"]:
                    return WebResponse.json(listing)
                review = review_contract_candidate(self.root, pack, candidate_id)
                return WebResponse.json(
                    listing
                    | {
                        "review": review,
                        "review_text": format_contract_review(review),
                        "promotable": bool(review["replay"]["passed"]),
                    }
                )
            if method == "POST" and rest == ["contract", "promote"]:
                payload = _read_json(body)
                candidate_id = payload.get("candidate_id")
                review = review_contract_candidate(
                    self.root, pack, candidate_id if isinstance(candidate_id, str) else None
                )
                if not review["replay"]["passed"]:
                    raise ValueError("not promotable: replay checks failed")
                promoted = promote_contract_candidate(self.root, pack, review)
                self._drop_session(pack.id)
                return WebResponse.json(promoted)
            if method == "POST" and rest == ["eval"]:
                payload = _read_json(body)
                suite = str(payload.get("suite") or "smoke")
                return WebResponse.json(
                    collect_evaluation(self.root, selected_app=pack.id, suite=suite)
                )
            if method == "GET" and rest == ["records"]:
                return self._records(pack, query)
            if method == "GET" and len(rest) == 2 and rest[0] == "records":
                return self._record(pack, rest[1])
            if method == "GET" and rest == ["workspace"]:
                timezone = (parse_qs(query).get("timezone") or ["UTC"])[0].strip() or "UTC"
                return WebResponse.json(
                    distill_workspace(self.root, pack, timezone=timezone).model_dump()
                )
            if method == "POST" and rest == ["workspace", "capture"]:
                payload = _read_json(body)
                collection = str(payload.get("collection") or "inbox")
                document = payload.get("document")
                if not isinstance(document, dict):
                    raise ValueError("document must be a JSON object")
                document_id = payload.get("document_id")
                timezone = str(payload.get("timezone") or "UTC").strip() or "UTC"
                captured = capture_document(
                    self.root,
                    pack,
                    collection=collection,
                    document=document,
                    document_id=document_id if isinstance(document_id, str) else None,
                )
                return WebResponse.json(
                    {
                        "record": captured,
                        "workspace": distill_workspace(
                            self.root, pack, timezone=timezone
                        ).model_dump(),
                    }
                )
            if method == "POST" and rest == ["workspace", "archive"]:
                payload = _read_json(body)
                document_id = str(payload.get("document_id") or "")
                reason = str(payload.get("reason") or "archived from workspace")
                archived = archive_document(
                    self.root, pack, document_id, reason=reason
                )
                timezone = str(payload.get("timezone") or "UTC").strip() or "UTC"
                return WebResponse.json(
                    {
                        "record": archived,
                        "workspace": distill_workspace(
                            self.root, pack, timezone=timezone
                        ).model_dump(),
                    }
                )
        return WebResponse.error("not found", status=404)

    def _index(self) -> WebResponse:
        path = STATIC_DIR / "index.html"
        if not path.is_file():
            raise RuntimeError("web interface files are missing")
        return WebResponse.html(path.read_bytes())

    def _runtime(self) -> WebResponse:
        try:
            config = LiteLLMConfig.from_environment()
        except ValueError as exc:
            return WebResponse.json({"configured": False, "error": str(exc)})
        return WebResponse.json({"configured": True, "model": config.model})

    def _packs(self) -> list[AppPack]:
        return discover_app_packs(self.root / "apps")

    def _pack(self, app_id: str) -> AppPack:
        pack = next((item for item in self._packs() if item.id == app_id), None)
        if pack is None:
            raise KeyError(f"unknown application {app_id!r}")
        return pack

    def _app_detail(self, pack: AppPack) -> dict[str, Any]:
        traces = self.root / "var" / "traces" / f"{pack.id}.jsonl"
        try:
            contract = list_contract_candidates(self.root, pack)
        except OSError:
            contract = {"current": None, "candidates": []}
        return _pack_summary(pack) | {
            "functionality": pack.functionality,
            "traces_available": traces.is_file() and traces.stat().st_size > 0,
            "improvements": list_improvement_candidates(self.root, pack),
            "contract": contract,
        }

    def _records(self, pack: AppPack, query: str) -> WebResponse:
        params = parse_qs(query)
        collection = params.get("collection", [None])[0] or None
        try:
            limit = int(params.get("limit", ["200"])[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be between 1 and 500") from exc
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        store = self._open_store(pack)
        if store is None:
            return WebResponse.json(
                {
                    "collections": {},
                    "collection": collection,
                    "total": 0,
                    "shown": 0,
                    "records": [],
                }
            )
        try:
            collections = store.describe()
            records = store.query(collection=collection, limit=limit)
            total = (
                sum(collections.values())
                if collection is None
                else int(collections.get(collection, 0))
            )
            return WebResponse.json(
                {
                    "collections": collections,
                    "collection": collection,
                    "total": total,
                    "shown": len(records),
                    "records": [item.payload() for item in records],
                }
            )
        finally:
            store.close()

    def _record(self, pack: AppPack, document_id: str) -> WebResponse:
        store = self._open_store(pack)
        if store is None:
            raise KeyError(document_id)
        try:
            document = store.get(document_id, include_archived=True)
            history = store.history(document_id)
            return WebResponse.json(
                {
                    "record": document.payload(),
                    "history": [item.payload() for item in history],
                }
            )
        finally:
            store.close()

    def _open_store(self, pack: AppPack) -> DocumentStore | None:
        database = database_path(self.root)
        if not database.is_file():
            return None
        return DocumentStore(database, app_id=pack.id)

    def _chat(self, pack: AppPack, body: bytes) -> WebResponse:
        payload = _read_json(body)
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")
        timezone = str(payload.get("timezone") or "UTC")
        debug = bool(payload.get("debug"))
        session = self._session_for(pack, timezone=timezone, debug=debug)
        outcome = session.send(message)
        view = view_from_tool_calls(outcome.get("tool_calls"))
        response: dict[str, Any] = {
            "reply": outcome["reply"],
            "view": None if view is None else view.model_dump(),
        }
        if debug:
            response["tool_calls"] = outcome.get("tool_calls") or []
        return WebResponse.json(response)

    def _session_for(self, pack: AppPack, *, timezone: str, debug: bool) -> ApplicationChat:
        with self._session_lock:
            existing = self._sessions.get(pack.id)
            if (
                existing is not None
                and existing.timezone == timezone
                and existing.debug == debug
            ):
                return existing
            if existing is not None:
                existing.close()
            session = ApplicationChat(self.root, pack, timezone=timezone, debug=debug)
            self._sessions[pack.id] = session
            return session

    def _drop_session(self, app_id: str) -> None:
        with self._session_lock:
            session = self._sessions.pop(app_id, None)
        if session is not None:
            session.close()

    def _improvement_payload(self, pack: AppPack, result: dict[str, Any]) -> dict[str, Any]:
        payload = dict(result)
        payload["proposal_text"] = format_improvement_proposal(pack, result)
        payload["promotable"] = improvement_is_promotable(result)
        if result.get("status") == "no-change":
            payload["diff"] = ""
            return payload
        candidate_root = Path(str(result.get("path") or ""))
        functionality_path = candidate_root / "FUNCTIONALITY.md"
        if functionality_path.is_file():
            revised = functionality_path.read_text(encoding="utf-8")
            payload["revised_functionality"] = revised
            payload["diff"] = "".join(
                unified_diff(
                    pack.functionality.splitlines(keepends=True),
                    revised.splitlines(keepends=True),
                    fromfile=f"{pack.id} {pack.version} (current)",
                    tofile=f"{result.get('candidate_id', 'candidate')} (candidate)",
                )
            ).rstrip()
        return payload


def _pack_summary(pack: AppPack) -> dict[str, Any]:
    return {
        "id": pack.id,
        "name": pack.name,
        "version": pack.version,
        "capabilities": list(pack.capabilities),
        "data_policy": pack.data_policy,
        "trace_mode": pack.trace_mode,
    }


def _read_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def make_handler(app: PromptWeb) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                response = WebResponse.error("request body is too large", status=413)
            else:
                body = self.rfile.read(length) if length else b""
                response = app.dispatch(method, self.path, body=body)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    return Handler


def run_web(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> int:
    app = PromptWeb(root)
    server = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host}:{port}/"
    print(f"Prompt OS web interface at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        app.close()
        server.server_close()
    return 0
