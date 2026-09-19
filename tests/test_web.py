from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from prompt_os.app_pack import AppPack
from prompt_os.cli import main
from prompt_os.store import DocumentStore
from prompt_os.web import PromptWeb, WebResponse


def _create_app(root: Path) -> AppPack:
    app_root = root / "apps" / "sample-app"
    app_root.mkdir(parents=True)
    (root / "apps" / "index.json").write_text(
        json.dumps({"applications": ["sample-app"]}), encoding="utf-8"
    )
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "id": "sample-app",
                "name": "Sample App",
                "version": "1.2.3",
                "functionality": "FUNCTIONALITY.md",
                "capabilities": ["documents"],
                "data_policy": "persistent",
            }
        ),
        encoding="utf-8",
    )
    (app_root / "FUNCTIONALITY.md").write_text(
        "# Purpose\n\nRecord things.\n", encoding="utf-8"
    )
    return AppPack.load(app_root)


def _create_candidate(root: Path, pack: AppPack) -> dict[str, object]:
    candidate_id = "candidate-1"
    candidate_root = root / "var" / "improvements" / pack.id / candidate_id
    candidate_root.mkdir(parents=True)
    (candidate_root / "FUNCTIONALITY.md").write_text(
        "# Purpose\n\nRecord and retrieve things.\n", encoding="utf-8"
    )
    report: dict[str, object] = {
        "candidate_id": candidate_id,
        "app_id": pack.id,
        "created_at": "2026-09-11T10:00:00+00:00",
        "source_version": pack.version,
        "source_functionality_sha256": hashlib.sha256(
            (pack.functionality.rstrip() + "\n").encode("utf-8")
        ).hexdigest(),
        "candidate_functionality_sha256": hashlib.sha256(
            (candidate_root / "FUNCTIONALITY.md").read_bytes()
        ).hexdigest(),
        "summary": "Allow retrieval",
        "rationale": "A trace showed retrieval was expected.",
        "evidence_trace_ids": ["trace-1"],
        "baseline": {
            "passed": 1,
            "total": 1,
            "cases": [{"id": "case-1", "passed": True}],
        },
        "candidate": {
            "passed": 1,
            "total": 1,
            "cases": [{"id": "case-1", "passed": True}],
        },
        "recommended": False,
        "status": "candidate",
    }
    (candidate_root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return report | {"path": str(candidate_root)}


class FakeChat:
    def __init__(self, outcome: dict[str, Any] | Exception) -> None:
        self.outcome = outcome
        self.prompts: list[str] = []
        self.closed = False
        self.model = "model"

    def send(self, message: str) -> dict[str, Any]:
        self.prompts.append(message)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def close(self) -> None:
        self.closed = True


def _json(response: WebResponse) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def test_web_lists_apps_and_serves_the_operator_interface(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    web = PromptWeb(tmp_path)

    listing = web.dispatch("GET", "/api/apps")
    page = web.dispatch("GET", "/")
    detail = web.dispatch("GET", f"/api/apps/{pack.id}")

    assert listing.status == 200
    assert _json(listing)["apps"][0]["id"] == pack.id
    assert page.status == 200
    assert page.content_type.startswith("text/html")
    assert b"Prompt OS" in page.body
    assert b"katex.min.js" in page.body
    assert b"renderMath" in page.body
    assert b"renderLineChart" in page.body
    assert b"pendingChat" in page.body
    assert b"nib-track" in page.body
    assert _json(detail)["functionality"].startswith("# Purpose")


def test_web_chat_renders_a_generic_view(tmp_path: Path, monkeypatch) -> None:
    pack = _create_app(tmp_path)
    chat = FakeChat(
        {
            "reply": "The answer is 4.",
            "tool_calls": [
                {
                    "name": "view.present",
                    "status": "success",
                    "arguments": {
                        "view": {
                            "title": "Result",
                            "blocks": [
                                {
                                    "type": "table",
                                    "columns": ["Item", "Value"],
                                    "rows": [["Answer", "4"]],
                                }
                            ],
                        }
                    },
                }
            ],
        }
    )
    monkeypatch.setattr("prompt_os.web.ApplicationChat", lambda *_args, **_kwargs: chat)
    web = PromptWeb(tmp_path)

    response = web.dispatch(
        "POST",
        f"/api/apps/{pack.id}/chat",
        body=json.dumps({"message": "add 2 and 2", "debug": True}).encode(),
    )

    payload = _json(response)
    assert response.status == 200
    assert chat.prompts == ["add 2 and 2"]
    assert payload["reply"] == "The answer is 4."
    assert payload["view"]["title"] == "Result"
    assert payload["tool_calls"][0]["name"] == "view.present"


def test_web_surfaces_chat_errors_and_unknown_apps(tmp_path: Path, monkeypatch) -> None:
    pack = _create_app(tmp_path)
    chat = FakeChat(RuntimeError("model unavailable"))
    monkeypatch.setattr("prompt_os.web.ApplicationChat", lambda *_args, **_kwargs: chat)
    web = PromptWeb(tmp_path)

    failed = web.dispatch(
        "POST",
        f"/api/apps/{pack.id}/chat",
        body=json.dumps({"message": "hello"}).encode(),
    )
    missing = web.dispatch("GET", "/api/apps/missing-app")

    assert failed.status == 400
    assert "model unavailable" in _json(failed)["error"]
    assert missing.status == 404


def test_web_promotes_a_replayed_improvement(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    _create_candidate(tmp_path, pack)
    web = PromptWeb(tmp_path)

    listed = web.dispatch("GET", f"/api/apps/{pack.id}/improvements")
    loaded = web.dispatch("GET", f"/api/apps/{pack.id}/improvements/candidate-1")
    promoted = web.dispatch(
        "POST", f"/api/apps/{pack.id}/improve/candidate-1/promote"
    )

    assert _json(listed)["candidates"][0]["candidate_id"] == "candidate-1"
    assert _json(loaded)["promotable"] is True
    assert "-Record things." in _json(loaded)["diff"]
    assert promoted.status == 200
    assert _json(promoted)["to_version"] == "1.2.4"
    assert AppPack.load(pack.path).version == "1.2.4"


def test_web_proposes_improvements_and_runs_eval_through_injected_services(
    tmp_path: Path, monkeypatch
) -> None:
    pack = _create_app(tmp_path)
    proposed = {
        "status": "no-change",
        "summary": "Traces already match the specification.",
        "rationale": "No repeated failure.",
    }
    evaluated = {
        "suite": "smoke",
        "app": pack.id,
        "passed": 1,
        "total": 1,
        "cases": [{"id": "case-1", "passed": True, "detail": "ok"}],
    }
    monkeypatch.setattr("prompt_os.web.improve", lambda *_args, **_kwargs: proposed)
    monkeypatch.setattr(
        "prompt_os.web.collect_evaluation", lambda *_args, **_kwargs: evaluated
    )
    web = PromptWeb(tmp_path)

    improvement = web.dispatch("POST", f"/api/apps/{pack.id}/improve")
    evaluation = web.dispatch(
        "POST",
        f"/api/apps/{pack.id}/eval",
        body=json.dumps({"suite": "smoke"}).encode(),
    )

    assert _json(improvement)["status"] == "no-change"
    assert _json(evaluation)["passed"] == 1


def test_web_cli_starts_the_local_interface(
    tmp_path: Path, monkeypatch
) -> None:
    _create_app(tmp_path)
    called: dict[str, object] = {}

    def fake_run_web(
        root: Path, *, host: str, port: int, open_browser: bool
    ) -> int:
        called.update(root=root, host=host, port=port, open_browser=open_browser)
        return 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("prompt_os.web.run_web", fake_run_web)

    assert main(["web", "--port", "9000", "--open"]) == 0
    assert called["port"] == 9000
    assert called["open_browser"] is True
    assert called["host"] == "127.0.0.1"


def test_web_lists_stored_records_as_json(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    web = PromptWeb(tmp_path)
    empty = web.dispatch("GET", f"/api/apps/{pack.id}/records")

    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put("inbox", {"label": "alpha"}, document_id="record-1")
    store.put("notes", {"label": "beta"}, document_id="record-2")
    store.put("inbox", {"label": "alpha-2"}, document_id="record-1")
    store.close()

    listing = web.dispatch("GET", f"/api/apps/{pack.id}/records")
    inbox = web.dispatch("GET", f"/api/apps/{pack.id}/records?collection=inbox")
    detail = web.dispatch("GET", f"/api/apps/{pack.id}/records/record-1")
    missing = web.dispatch("GET", f"/api/apps/{pack.id}/records/missing")

    assert empty.status == 200
    assert _json(empty)["records"] == []
    assert _json(listing)["collections"] == {"inbox": 1, "notes": 1}
    assert _json(listing)["total"] == 2
    assert {item["id"] for item in _json(listing)["records"]} == {"record-1", "record-2"}
    assert [item["id"] for item in _json(inbox)["records"]] == ["record-1"]
    assert _json(inbox)["records"][0]["document"] == {"label": "alpha-2"}
    assert _json(detail)["record"]["document"] == {"label": "alpha-2"}
    assert [item["event"] for item in _json(detail)["history"]] == ["created", "updated"]
    assert missing.status == 404
    page = web.dispatch("GET", "/")
    assert b'data-tab="records"' in page.body
    assert b'data-tab="workspace"' in page.body
    assert b"operate-toggle" in page.body


def test_web_workspace_captures_without_a_conversation(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put("inbox", {"label": "alpha"}, document_id="record-1")
    store.close()
    web = PromptWeb(tmp_path)

    surface = web.dispatch("GET", f"/api/apps/{pack.id}/workspace")
    captured = web.dispatch(
        "POST",
        f"/api/apps/{pack.id}/workspace/capture",
        body=json.dumps(
            {"collection": "inbox", "document": {"label": "beta"}}
        ).encode(),
    )

    payload = _json(surface)
    assert surface.status == 200
    assert payload["source"] == "observed"
    assert payload["forms"][0]["collection"] == "inbox"
    assert captured.status == 200
    assert _json(captured)["record"]["document"] == {"label": "beta"}
    assert len(_json(captured)["workspace"]["records"]) == 2


def test_web_resets_a_conversation_session(tmp_path: Path, monkeypatch) -> None:
    pack = _create_app(tmp_path)
    chat = FakeChat({"reply": "ok", "tool_calls": []})
    monkeypatch.setattr("prompt_os.web.ApplicationChat", lambda *_args, **_kwargs: chat)
    web = PromptWeb(tmp_path)

    web.dispatch(
        "POST",
        f"/api/apps/{pack.id}/chat",
        body=json.dumps({"message": "hello"}).encode(),
    )
    reset = web.dispatch("POST", f"/api/apps/{pack.id}/session/reset")

    assert reset.status == 200
    assert chat.closed is True
