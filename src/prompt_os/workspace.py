from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from .app_pack import AppPack
from .data_contract import DataContractRepository
from .store import COLLECTION, DECIMAL, Document, DocumentStore
from .tool_service import ToolService
from .view_description import (
    BarChartBlock,
    BarDatum,
    MetricBlock,
    TableBlock,
    TextBlock,
    ViewDescription,
)


FormType = Literal[
    "string", "integer", "number", "decimal", "boolean", "datetime", "text", "list"
]


class FormField(BaseModel):
    name: str
    label: str
    type: FormType
    required: bool = False
    description: str | None = None
    options: list[str] | None = None
    default: str | None = None


class FormDescription(BaseModel):
    id: str
    collection: str
    title: str
    description: str | None = None
    mode: Literal["fixed", "open"] = "fixed"
    fields: list[FormField] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


class WorkspaceDescription(BaseModel):
    title: str
    source: Literal["stateless", "interface", "contract", "observed", "empty"]
    summary: str | None = None
    forms: list[FormDescription] = Field(default_factory=list)
    view: ViewDescription | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


_PURPOSE = re.compile(r"## Purpose\s+(.+?)(?:\n## |\Z)", re.DOTALL)
_INTERFACE = re.compile(r"## Interface\s+(.+?)(?:\n## |\Z)", re.DOTALL)
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")
_TYPE_RANK = {
    "decimal": 0,
    "number": 1,
    "integer": 2,
    "datetime": 3,
    "string": 4,
    "text": 5,
    "boolean": 6,
    "list": 7,
}


def distill_workspace(
    root: Path, pack: AppPack, *, timezone: str = "UTC"
) -> WorkspaceDescription:
    """Derive generic forms and views from the specification, contract or records."""
    summary = _purpose_text(pack.functionality)
    if "documents" not in pack.capabilities:
        return WorkspaceDescription(
            title=pack.name,
            source="stateless",
            summary=summary,
            view=ViewDescription(
                title=pack.name,
                blocks=[
                    TextBlock(
                        type="text",
                        markdown="This application does not keep records. Use conversation for its services.",
                    )
                ],
            ),
        )

    documents, counts = _load_documents(root, pack)
    interface = parse_interface(pack.functionality)
    contract = _current_contract(root, pack)
    source: Literal["stateless", "interface", "contract", "observed", "empty"]
    if interface:
        forms = _forms_from_interface(interface, documents)
        source = "interface"
        preferred = [field.name for field in forms[0].fields] if forms else None
    elif contract:
        forms = [
            _form_from_contract(name, spec)
            for name, spec in contract.get("collections", {}).items()
            if isinstance(spec, dict)
        ]
        source = "contract"
        preferred = _contract_columns(contract)
    else:
        forms = _forms_from_documents(documents)
        source = "observed" if documents else "empty"
        preferred = None
    _apply_date_defaults(forms, timezone)
    records = [
        _record_payload(document, title=_record_title(document, forms))
        for document in documents
    ]
    hints = interface.get("hints") if interface else None
    filters = _filters_from_hints(hints)
    return WorkspaceDescription(
        title=pack.name,
        source=source,
        summary=summary or (contract.get("summary") if contract else None),
        forms=forms,
        view=_view_from_records(
            pack.name,
            documents,
            counts,
            forms=forms,
            preferred_columns=preferred,
            hints=hints,
            filters=filters,
        ),
        records=records,
        filters=filters,
    )


def _local_today(timezone: str) -> str:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        zone = ZoneInfo("UTC")
    return datetime.now(zone).date().isoformat()


def _apply_date_defaults(forms: list[FormDescription], timezone: str) -> None:
    today = _local_today(timezone)
    for form in forms:
        for field in form.fields:
            if field.type == "datetime":
                field.default = today


def parse_interface(functionality: str) -> dict[str, Any] | None:
    """Read an optional, lenient Interface section from a business specification."""
    match = _INTERFACE.search(functionality)
    if match is None:
        return None
    body = match.group(1).strip()
    if not body:
        return None
    capture_text, show_text = _split_capture_show(body)
    fields = _parse_capture_fields(capture_text)
    if not fields:
        return None
    return {"fields": fields, "hints": _parse_show_hints(show_text)}


def _split_capture_show(body: str) -> tuple[str, str]:
    show = re.search(r"(?im)^\s*show\s*:?\s*$", body)
    if show:
        return body[: show.start()].strip(), body[show.end() :].strip()
    show_inline = re.search(r"(?i)\bshow\s*:", body)
    if show_inline:
        return body[: show_inline.start()].strip(), body[show_inline.end() :].strip()
    return body.strip(), ""


def _parse_capture_fields(text: str) -> list[FormField]:
    text = re.sub(r"(?is)^\s*capture\s*:?\s*", "", text).strip()
    if not text:
        return []
    bullets = re.findall(r"^[\-\*]\s+(.+)$", text, re.MULTILINE)
    phrases = bullets if bullets else _split_phrases(text)
    fields: list[FormField] = []
    seen: set[str] = set()
    for phrase in phrases:
        for field in _fields_from_phrase(phrase):
            if field.name in seen:
                continue
            seen.add(field.name)
            fields.append(field)
    return fields


def _split_phrases(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r",|;|\band\b", text, flags=re.I)
    return [part.strip() for part in parts if part.strip()]


def _fields_from_phrase(phrase: str) -> list[FormField]:
    return [_parse_field_phrase(part) for part in re.split(r"\bor\b", phrase, flags=re.I) if part.strip()]


def _parse_field_phrase(phrase: str) -> FormField:
    required = bool(re.search(r"\brequired\b", phrase, re.I))
    type_hint: FormType | None = None
    for word, field_type in (
        ("decimal", "decimal"),
        ("number", "number"),
        ("integer", "integer"),
        ("boolean", "boolean"),
        ("datetime", "datetime"),
        ("date", "datetime"),
        ("text", "text"),
        ("list", "list"),
    ):
        if re.search(rf"\b{word}\b", phrase, re.I):
            type_hint = field_type  # type: ignore[assignment]
            break
    name = _slug(re.sub(r"\([^)]*\)", "", phrase))
    if not _FIELD_NAME.fullmatch(name):
        name = re.sub(r"[^a-z0-9_]", "", name) or "note"
        if name[0].isdigit():
            name = f"field_{name}"
    return FormField(
        name=name,
        label=_label(name),
        type=type_hint or _type_from_name(name),
        required=required,
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    return slug or "note"


def _type_from_name(name: str) -> FormType:
    parts = set(name.split("_"))
    if "date" in name or name.endswith("_at"):
        return "datetime"
    if parts & {"amount", "quantity", "count", "total", "duration", "price", "qty"}:
        return "decimal"
    if name.startswith("is_") or name in {"reimbursable", "completed"}:
        return "boolean"
    if parts & {"note", "notes", "rationale", "context"}:
        return "text"
    return "string"


def _parse_show_hints(text: str) -> list[dict[str, str]]:
    text = re.sub(r"(?is)^\s*show\s*:?\s*", "", text).strip()
    if not text:
        return [{"kind": "table"}]
    bullets = re.findall(r"^[\-\*]\s+(.+)$", text, re.MULTILINE)
    phrases = bullets if bullets else _split_phrases(text)
    hints: list[dict[str, str]] = [{"kind": "table"}]
    for phrase in phrases:
        lowered = phrase.casefold().strip()
        low = re.search(
            r"low[\s-]?stock(?:\s+(?:of\s+)?(.+?)\s+below\s+(.+))?", lowered
        )
        balance = re.search(
            r"(?:current\s+)?balance(?:\s+of)?\s+(.+?)\s+by\s+(.+)", lowered
        )
        grouped = re.search(r"(.+?)\s+by\s+(.+)", lowered)
        totaled = re.search(r"(?:total|totals|sum)\s+(?:of\s+)?([a-z0-9_ ]+)", lowered)
        if re.search(r"\bopen\s+only\b", lowered):
            hints.append({"kind": "open"})
        elif low:
            hint = {"kind": "low-stock"}
            if low.group(1) and low.group(2):
                hint["field"] = _slug(low.group(1))
                hint["threshold"] = _slug(low.group(2))
            hints.append(hint)
        elif balance:
            hints.append(
                {
                    "kind": "balance",
                    "field": _slug(balance.group(1)),
                    "group_by": _slug(balance.group(2)),
                }
            )
        elif grouped and not re.search(r"\b(total|sum|balance)\b", grouped.group(1)):
            hints.append(
                {
                    "kind": "group",
                    "field": _slug(grouped.group(1)),
                    "group_by": _slug(grouped.group(2)),
                }
            )
        elif totaled:
            hints.append({"kind": "total", "field": _slug(totaled.group(1))})
    return hints


def _filters_from_hints(hints: list[dict[str, str]] | None) -> dict[str, Any]:
    if not hints:
        return {}
    if any(hint.get("kind") == "open" for hint in hints):
        return {"open_only": True}
    return {}


def parse_field_value(field_type: str, raw: Any) -> Any:
    if field_type == "boolean":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    if field_type == "integer":
        return int(text)
    if field_type == "number":
        return float(text)
    if field_type == "list":
        if text.startswith("["):
            value = json.loads(text)
            if not isinstance(value, list):
                raise ValueError("list field must be a JSON array")
            return value
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


def capture_document(
    root: Path,
    pack: AppPack,
    *,
    collection: str,
    document: Mapping[str, Any],
    document_id: str | None = None,
) -> dict[str, Any]:
    if "documents" not in pack.capabilities:
        raise ValueError("this application does not keep records")
    if not isinstance(document, dict) or not document:
        raise ValueError("document must be a non-empty object")
    if not COLLECTION.fullmatch(collection):
        raise ValueError("invalid collection name")
    cleaned = {str(key): value for key, value in document.items() if str(key).strip()}
    if not cleaned:
        raise ValueError("document must be a non-empty object")

    database = root / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    if document_id:
        existing = _existing_document(root, pack, document_id)
        if existing is not None:
            cleaned = {**existing, **cleaned}

    schema = root / "contracts" / "data-contract.schema.json"
    if schema.is_file():
        service = ToolService(
            app_id=pack.id,
            database=database,
            contract_root=root / "var" / "data-contracts",
            contract_schema=schema,
            timezone="UTC",
            functionality_sha256=hashlib.sha256(
                (pack.functionality.rstrip() + "\n").encode("utf-8")
            ).hexdigest(),
        )
        try:
            return service.put(
                cleaned, collection=collection, document_id=document_id
            )
        finally:
            service.close()

    store = DocumentStore(database, app_id=pack.id)
    try:
        stored = store.put(collection, cleaned, document_id)
        return _record_payload(stored, title=None)
    finally:
        store.close()


def archive_document(
    root: Path, pack: AppPack, document_id: str, *, reason: str = "archived from workspace"
) -> dict[str, Any]:
    if "documents" not in pack.capabilities:
        raise ValueError("this application does not keep records")
    if not document_id.strip():
        raise ValueError("document id is required")
    database = root / "var" / "prompt-os.sqlite"
    if not database.is_file():
        raise KeyError(document_id)
    schema = root / "contracts" / "data-contract.schema.json"
    if schema.is_file():
        service = ToolService(
            app_id=pack.id,
            database=database,
            contract_root=root / "var" / "data-contracts",
            contract_schema=schema,
            timezone="UTC",
            functionality_sha256=hashlib.sha256(
                (pack.functionality.rstrip() + "\n").encode("utf-8")
            ).hexdigest(),
        )
        try:
            return service.archive(document_id, reason)
        finally:
            service.close()
    store = DocumentStore(database, app_id=pack.id)
    try:
        return _record_payload(store.archive(document_id, reason), title=None)
    finally:
        store.close()


def _load_documents(root: Path, pack: AppPack) -> tuple[list[Document], dict[str, int]]:
    database = root / "var" / "prompt-os.sqlite"
    if not database.is_file():
        return [], {}
    store = DocumentStore(database, app_id=pack.id)
    try:
        return store.scan(limit=200), store.describe()
    finally:
        store.close()


def _current_contract(root: Path, pack: AppPack) -> dict[str, Any] | None:
    schema = root / "contracts" / "data-contract.schema.json"
    if not schema.is_file():
        return None
    try:
        return DataContractRepository(
            root / "var" / "data-contracts", schema
        ).current(pack.id)
    except (OSError, ValueError):
        return None


def _purpose_text(functionality: str) -> str | None:
    match = _PURPOSE.search(functionality)
    if match is None:
        return None
    text = " ".join(match.group(1).split())
    return text or None


def _existing_document(root: Path, pack: AppPack, document_id: str) -> dict[str, Any] | None:
    database = root / "var" / "prompt-os.sqlite"
    if not database.is_file():
        return None
    store = DocumentStore(database, app_id=pack.id)
    try:
        return dict(store.get(document_id, include_archived=True).value)
    except KeyError:
        return None
    finally:
        store.close()


def _forms_from_interface(
    interface: Mapping[str, Any], documents: list[Document]
) -> list[FormDescription]:
    collection = _default_collection(documents)
    inferred, defaults = _infer_fields(documents) if documents else ([], {})
    fields = _enrich_interface_fields(list(interface["fields"]), inferred)
    return [
        FormDescription(
            id=collection,
            collection=collection,
            title=_label(collection),
            mode="open",
            fields=fields,
            defaults=defaults,
        )
    ]


def _enrich_interface_fields(
    fields: list[FormField], inferred: list[FormField]
) -> list[FormField]:
    by_name = {field.name: field for field in inferred}
    enriched: list[FormField] = []
    for field in fields:
        observed = by_name.get(field.name)
        if observed is None:
            observed = next(
                (
                    item
                    for item in inferred
                    if item.name.endswith(f"_{field.name}")
                    or field.name.endswith(item.name)
                    or field.name in item.name
                ),
                None,
            )
        if observed is None and field.name in {"note", "decision"}:
            observed = next(
                (
                    item
                    for item in inferred
                    if item.name.endswith("_statement") or item.name in {"note", "decision"}
                ),
                None,
            )
        if observed is None:
            enriched.append(field)
            continue
        updates: dict[str, Any] = {
            "options": observed.options,
            "default": observed.default,
        }
        if field.type == "string" and observed.type != "string":
            updates["type"] = observed.type
        if observed.required and not field.required:
            updates["required"] = True
        enriched.append(field.model_copy(update=updates))
    return enriched


def _default_collection(documents: list[Document]) -> str:
    if not documents:
        return "inbox"
    counts: dict[str, int] = {}
    for document in documents:
        counts[document.collection] = counts.get(document.collection, 0) + 1
    return max(counts, key=lambda name: counts[name])


def _forms_from_documents(documents: list[Document]) -> list[FormDescription]:
    grouped: dict[str, list[Document]] = {}
    for document in documents:
        grouped.setdefault(document.collection, []).append(document)
    if not grouped:
        return [
            FormDescription(
                id="inbox",
                collection="inbox",
                title="inbox",
                description="No structure yet. Add named fields, or record a note.",
                mode="open",
                fields=[
                    FormField(
                        name="note",
                        label="Note",
                        type="text",
                        required=False,
                        description="A first record in ordinary language.",
                    )
                ],
            )
        ]
    return [
        _form_from_observed(name, items) for name, items in sorted(grouped.items())
    ]


def _form_from_observed(name: str, documents: list[Document]) -> FormDescription:
    fields, defaults = _infer_fields(documents)
    return FormDescription(
        id=name,
        collection=name,
        title=_label(name),
        description=None,
        mode="open",
        fields=fields,
        defaults=defaults,
    )


def _form_from_contract(name: str, spec: Mapping[str, Any]) -> FormDescription:
    fields = spec.get("fields") if isinstance(spec.get("fields"), dict) else {}
    return FormDescription(
        id=name,
        collection=name,
        title=name.replace("-", " ").replace("_", " "),
        description=str(spec.get("description") or "") or None,
        mode="fixed",
        fields=[
            FormField(
                name=field_name,
                label=_label(field_name),
                type=_form_type(field.get("type")),
                required=bool(field.get("required")),
                description=str(field.get("description") or "") or None,
            )
            for field_name, field in fields.items()
            if isinstance(field, dict)
        ],
    )


def _contract_columns(contract: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for spec in contract.get("collections", {}).values():
        if not isinstance(spec, dict) or not isinstance(spec.get("fields"), dict):
            continue
        for field_name in spec["fields"]:
            if field_name not in names:
                names.append(field_name)
    return names[:8]


def _infer_fields(documents: list[Document]) -> tuple[list[FormField], dict[str, Any]]:
    values: dict[str, list[Any]] = {}
    for document in documents:
        if not isinstance(document.value, dict):
            continue
        for key, value in document.value.items():
            if not isinstance(key, str) or not key.strip() or _is_stamp_name(key):
                continue
            values.setdefault(key, []).append(value)
    ranked: list[tuple[tuple[Any, ...], FormField]] = []
    defaults: dict[str, Any] = {}
    total = max(len(documents), 1)
    string_fields = 0
    for samples in values.values():
        present = [value for value in samples if value is not None and value != ""]
        if _infer_type(present) in {"string", "text"}:
            string_fields += 1
    for name, samples in values.items():
        present = [value for value in samples if value is not None and value != ""]
        field_type = _infer_type(present)
        coverage = len(present) / total
        if _is_hidden_constant(field_type, present, coverage, string_fields):
            defaults[name] = present[0]
            continue
        options = _infer_options(field_type, present)
        default = None
        if options:
            default = max(options, key=lambda item: sum(str(value) == item for value in present))
        ranked.append(
            (
                (_TYPE_RANK.get(field_type, 9), -coverage, name),
                FormField(
                    name=name,
                    label=_label(name),
                    type=field_type,
                    required=coverage >= 0.8 and field_type != "boolean",
                    options=options,
                    default=None if default is None else str(default),
                ),
            )
        )
    ranked.sort(key=lambda item: item[0])
    return [field for _, field in ranked[:12]], defaults


def _infer_type(samples: list[Any]) -> FormType:
    present = [value for value in samples if value is not None]
    if not present:
        return "string"
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return "integer"
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in present
    ):
        return "number"
    if all(isinstance(value, str) and DECIMAL.fullmatch(value) for value in present):
        return "decimal"
    if all(isinstance(value, str) and _ISO_DATE.fullmatch(value) for value in present):
        return "datetime"
    if all(isinstance(value, list) for value in present):
        return "list"
    if any(isinstance(value, str) and "\n" in value for value in present):
        return "text"
    return "string"


def _infer_options(field_type: FormType, present: list[Any]) -> list[str] | None:
    if field_type not in {"string", "datetime"}:
        return None
    unique = sorted({str(value) for value in present if str(value).strip()})
    if 2 <= len(unique) <= 12 and len(unique) < len(present):
        return unique
    return None


def _is_stamp_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered.endswith("_at")
        or lowered.endswith("_time")
        or lowered.endswith("_status")
        or lowered == "timezone"
    )


def _is_hidden_constant(
    field_type: FormType, present: list[Any], coverage: float, string_fields: int
) -> bool:
    if field_type not in {"string", "text"} or coverage < 0.9 or string_fields < 2:
        return False
    return len({str(value) for value in present}) == 1


def _form_type(declared: Any) -> FormType:
    mapping: dict[str, FormType] = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "decimal": "decimal",
        "boolean": "boolean",
        "datetime": "datetime",
        "reference": "string",
        "list": "list",
    }
    return mapping.get(str(declared), "string")


def _label(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip() or name


def _record_title(document: Document, forms: list[FormDescription]) -> str:
    form = next((item for item in forms if item.collection == document.collection), None)
    fields = list(form.fields) if form else []
    preferred = [
        field
        for field in fields
        if field.type in {"string", "text"} and not field.options
    ] + [
        field
        for field in fields
        if field.type in {"decimal", "number", "integer", "datetime"}
    ]
    parts: list[str] = []
    value = document.value if isinstance(document.value, dict) else {}
    for field in preferred:
        cell = _cell(value.get(field.name))
        if cell:
            parts.append(cell)
        if len(parts) == 3:
            break
    return " · ".join(parts) or document.id


def _view_from_records(
    title: str,
    documents: list[Document],
    counts: dict[str, int],
    *,
    forms: list[FormDescription],
    preferred_columns: list[str] | None,
    hints: list[dict[str, str]] | None = None,
    filters: dict[str, Any] | None = None,
) -> ViewDescription | None:
    blocks: list[Any] = []
    if counts:
        blocks.append(
            MetricBlock(
                type="metric",
                label="Records",
                value=str(sum(counts.values())),
                detail=", ".join(f"{name} {count}" for name, count in sorted(counts.items())),
            )
        )
    if hints:
        blocks.extend(_views_from_hints(documents, hints, forms))
    else:
        blocks.extend(_numeric_metrics(documents, forms))
        blocks.extend(_grouped_charts(documents, forms))
    table_docs = documents
    if filters and filters.get("open_only"):
        table_docs = [document for document in documents if _is_open(document)]
        blocks.append(
            MetricBlock(
                type="metric",
                label="Open",
                value=str(len(table_docs)),
                detail="excluding completed records",
            )
        )
    if table_docs:
        columns = _table_columns(table_docs, forms, preferred_columns)
        blocks.append(
            TableBlock(
                type="table",
                title="Open records" if filters and filters.get("open_only") else "Current records",
                columns=columns,
                rows=[_table_row(document, columns) for document in table_docs],
            )
        )
    if not blocks:
        return None
    return ViewDescription(title=title, blocks=blocks)


_CLOSED = {
    "done",
    "complete",
    "completed",
    "closed",
    "archived",
    "cancelled",
    "canceled",
}


def _is_open(document: Document) -> bool:
    value = document.value if isinstance(document.value, dict) else {}
    if value.get("completed") is True:
        return False
    status = value.get("status")
    if status is None or str(status).strip() == "":
        return True
    return str(status).casefold() not in _CLOSED


def _resolve_field(
    forms: list[FormDescription], types: set[str], *, fallback: str
) -> str:
    for form in forms:
        for field in form.fields:
            if field.type in types:
                return field.name
    return fallback


def _resolve_named_field(forms: list[FormDescription], names: set[str]) -> str | None:
    for form in forms:
        for field in form.fields:
            if field.name in names:
                return field.name
    return None


def _numeric_by_key(
    documents: list[Document], field: str, group_by: str
) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for document in documents:
        if not isinstance(document.value, dict):
            continue
        key = document.value.get(group_by)
        value = document.value.get(field)
        if key in (None, "") or value in (None, ""):
            continue
        try:
            totals[str(key)] = totals.get(str(key), Decimal(0)) + Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
    return totals


def _latest_value(
    documents: list[Document], group_by: str, field: str
) -> dict[str, str]:
    latest: dict[str, str] = {}
    for document in documents:
        if not isinstance(document.value, dict):
            continue
        key = document.value.get(group_by)
        value = document.value.get(field)
        if key in (None, "") or value in (None, "") or str(key) in latest:
            continue
        latest[str(key)] = str(value)
    return latest


def _balance_blocks(
    documents: list[Document], field: str, group_by: str, extra: str | None
) -> list[MetricBlock | TableBlock]:
    totals = _numeric_by_key(documents, field, group_by)
    if not totals:
        return []
    extras = _latest_value(documents, group_by, extra) if extra else {}
    columns = [_label(group_by), "balance"] + ([_label(extra)] if extra else [])
    rows = []
    for key, total in sorted(totals.items()):
        row = [key, format(total.normalize(), "f")]
        if extra:
            row.append(extras.get(key, ""))
        rows.append(row)
    return [
        MetricBlock(
            type="metric",
            label="Balance",
            value=format(sum(totals.values()).normalize(), "f"),
            detail=f"{len(totals)} {_label(group_by)}",
        ),
        TableBlock(type="table", title="Current balance", columns=columns, rows=rows),
    ]


def _low_stock_blocks(
    documents: list[Document], field: str, group_by: str, threshold: str
) -> list[TableBlock]:
    totals = _numeric_by_key(documents, field, group_by)
    levels = _latest_value(documents, group_by, threshold)
    rows: list[list[str]] = []
    for key, total in sorted(totals.items()):
        level = levels.get(key)
        if not level:
            continue
        try:
            limit = Decimal(level)
        except (InvalidOperation, ValueError):
            continue
        if total < limit:
            rows.append(
                [key, format(total.normalize(), "f"), format(limit.normalize(), "f")]
            )
    if not rows:
        return []
    return [
        TableBlock(
            type="table",
            title="Low stock",
            columns=[_label(group_by), "balance", _label(threshold)],
            rows=rows,
        )
    ]


def _views_from_hints(
    documents: list[Document],
    hints: list[dict[str, str]],
    forms: list[FormDescription],
) -> list[MetricBlock | BarChartBlock | TableBlock]:
    blocks: list[MetricBlock | BarChartBlock | TableBlock] = []
    identity = _resolve_field(forms, {"string", "text"}, fallback="item")
    amount = _resolve_field(forms, {"decimal", "number", "integer"}, fallback="quantity")
    threshold = _resolve_named_field(forms, {"replenishment", "reorder", "threshold", "level"})
    for hint in hints:
        if hint.get("kind") == "balance":
            field = hint.get("field") or amount
            group_by = hint.get("group_by") or identity
            blocks.extend(_balance_blocks(documents, field, group_by, threshold))
        elif hint.get("kind") == "low-stock":
            field = hint.get("field") or amount
            group_by = hint.get("group_by") or identity
            level = hint.get("threshold") or threshold or "replenishment"
            blocks.extend(_low_stock_blocks(documents, field, group_by, level))
        elif hint.get("kind") == "total" and hint.get("field"):
            field = hint["field"]
            total = Decimal(0)
            used = 0
            for document in documents:
                if not isinstance(document.value, dict):
                    continue
                value = document.value.get(field)
                if value in (None, ""):
                    continue
                try:
                    total += Decimal(str(value))
                except (InvalidOperation, ValueError):
                    continue
                used += 1
            if used:
                blocks.append(
                    MetricBlock(
                        type="metric",
                        label=_label(field),
                        value=format(total.normalize(), "f"),
                        detail=f"{used} records",
                    )
                )
        elif hint.get("kind") == "group" and hint.get("field") and hint.get("group_by"):
            field, group_by = hint["field"], hint["group_by"]
            totals: dict[str, Decimal] = {}
            for document in documents:
                if not isinstance(document.value, dict):
                    continue
                key = document.value.get(group_by)
                value = document.value.get(field)
                if key in (None, "") or value in (None, ""):
                    continue
                try:
                    totals[str(key)] = totals.get(str(key), Decimal(0)) + Decimal(str(value))
                except (InvalidOperation, ValueError):
                    continue
            if len(totals) >= 2:
                blocks.append(
                    BarChartBlock(
                        type="bar-chart",
                        title=f"{_label(field)} by {_label(group_by)}",
                        series=[
                            BarDatum(
                                label=label,
                                value=float(total),
                                display=format(total.normalize(), "f"),
                            )
                            for label, total in sorted(
                                totals.items(), key=lambda item: item[1], reverse=True
                            )
                        ],
                    )
                )
    return blocks


def _numeric_metrics(
    documents: list[Document], forms: list[FormDescription]
) -> list[MetricBlock]:
    blocks: list[MetricBlock] = []
    for form in forms:
        scoped = [item for item in documents if item.collection == form.collection]
        for field in form.fields:
            if field.type not in {"decimal", "number", "integer"}:
                continue
            total = Decimal(0)
            used = 0
            for document in scoped:
                if not isinstance(document.value, dict):
                    continue
                value = document.value.get(field.name)
                if value in (None, ""):
                    continue
                try:
                    total += Decimal(str(value))
                except (InvalidOperation, ValueError):
                    continue
                used += 1
            if used:
                blocks.append(
                    MetricBlock(
                        type="metric",
                        label=_label(field.name),
                        value=format(total.normalize(), "f"),
                        detail=f"{used} in {form.collection}",
                    )
                )
    return blocks[:4]


def _grouped_charts(
    documents: list[Document], forms: list[FormDescription]
) -> list[BarChartBlock]:
    charts: list[BarChartBlock] = []
    for form in forms:
        numeric = [
            field
            for field in form.fields
            if field.type in {"decimal", "number", "integer"}
        ]
        grouped = [
            field
            for field in form.fields
            if field.options and len(field.options) >= 2
        ]
        if not numeric or not grouped:
            continue
        amount, category = numeric[0], grouped[0]
        totals: dict[str, Decimal] = {}
        for document in documents:
            if document.collection != form.collection or not isinstance(document.value, dict):
                continue
            key = document.value.get(category.name)
            value = document.value.get(amount.name)
            if key in (None, "") or value in (None, ""):
                continue
            try:
                totals[str(key)] = totals.get(str(key), Decimal(0)) + Decimal(str(value))
            except (InvalidOperation, ValueError):
                continue
        if len(totals) < 2:
            continue
        series = [
            BarDatum(label=label, value=float(total), display=format(total.normalize(), "f"))
            for label, total in sorted(totals.items(), key=lambda item: item[1], reverse=True)
        ]
        charts.append(
            BarChartBlock(
                type="bar-chart",
                title=f"{_label(amount.name)} by {_label(category.name)}",
                series=series,
            )
        )
    return charts[:2]


def _table_columns(
    documents: list[Document],
    forms: list[FormDescription],
    preferred: list[str] | None,
) -> list[str]:
    names = [name for name in (preferred or []) if not _is_stamp_name(name)]
    if not names:
        for form in forms:
            for field in form.fields:
                if field.name not in names:
                    names.append(field.name)
    if not names:
        for document in documents:
            if not isinstance(document.value, dict):
                continue
            for key in document.value:
                if isinstance(key, str) and key not in names and not _is_stamp_name(key):
                    names.append(key)
    collections = {document.collection for document in documents}
    prefix = ["collection"] if len(collections) > 1 else []
    return [*prefix, *names[:6]] or ["id"]


def _table_row(document: Document, columns: list[str]) -> list[str]:
    value = document.value if isinstance(document.value, dict) else {}
    cells: list[str] = []
    for column in columns:
        if column == "id":
            cells.append(document.id)
        elif column == "collection":
            cells.append(document.collection)
        else:
            cells.append(_cell(value.get(column)))
    return cells


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _record_payload(document: Document, title: str | None) -> dict[str, Any]:
    return {
        "id": document.id,
        "collection": document.collection,
        "title": title or document.id,
        "document": document.value,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "archived_at": document.archived_at,
        "archive_reason": document.archive_reason,
        "revision": document.revision,
    }
