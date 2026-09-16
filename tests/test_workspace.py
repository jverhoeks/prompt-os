from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from prompt_os.app_pack import AppPack
from prompt_os.store import DocumentStore
from prompt_os.workspace import capture_document, distill_workspace, parse_interface


def _pack(root: Path, *, capabilities: tuple[str, ...] = ("documents",)) -> AppPack:
    app_root = root / "apps" / "sample-app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "id": "sample-app",
                "name": "Sample App",
                "version": "0.1.0",
                "functionality": "FUNCTIONALITY.md",
                "capabilities": list(capabilities),
                "data_policy": "persistent",
            }
        ),
        encoding="utf-8",
    )
    (app_root / "FUNCTIONALITY.md").write_text(
        "## Purpose\n\nRecord ordinary things.\n\n## Services\n\n- Capture a record.\n",
        encoding="utf-8",
    )
    return AppPack.load(app_root)


def test_interface_section_is_read_leniently_from_requirements() -> None:
    spec = parse_interface(
        "## Purpose\n\nKeep notes.\n\n## Interface\n\n"
        "Capture:\n- label (required)\n- count (decimal)\n\n"
        "Show:\n- records\n- total of count\n- count by label\n"
    )

    assert spec is not None
    names = [field.name for field in spec["fields"]]
    assert names == ["label", "count"]
    assert spec["fields"][0].required is True
    assert spec["fields"][1].type == "decimal"
    kinds = {hint["kind"] for hint in spec["hints"]}
    assert {"table", "total", "group"} <= kinds


def test_interface_names_balance_low_stock_and_open_views() -> None:
    spec = parse_interface(
        "## Interface\n\nCapture:\n- item (required)\n- quantity (decimal)\n"
        "- replenishment (decimal)\n\n"
        "Show:\n- current balance of quantity by item\n"
        "- low stock of quantity below replenishment\n- open only\n"
    )

    assert spec is not None
    kinds = {hint["kind"] for hint in spec["hints"]}
    assert {"balance", "low-stock", "open"} <= kinds


def test_workspace_prefers_interface_over_observed_keys(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    (pack.path / "FUNCTIONALITY.md").write_text(
        "## Purpose\n\nRecord ordinary things.\n\n"
        "## Services\n\n- Capture a record.\n\n"
        "## Interface\n\nCapture:\n- label (required)\n- count (decimal)\n\n"
        "Show:\n- records\n- total of count\n",
        encoding="utf-8",
    )
    pack = AppPack.load(pack.path)
    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put("inbox", {"label": "alpha", "count": "2", "noise": "x"})
    store.close()

    surface = distill_workspace(tmp_path, pack)
    names = [field.name for field in surface.forms[0].fields]

    assert surface.source == "interface"
    assert names == ["label", "count"]
    assert "noise" not in names


def test_date_fields_default_to_today_in_the_configured_timezone(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    (pack.path / "FUNCTIONALITY.md").write_text(
        "## Purpose\n\nRecord events.\n\n## Services\n\n- Capture a record.\n\n"
        "## Interface\n\nCapture:\n- label (required)\n- date\n",
        encoding="utf-8",
    )
    pack = AppPack.load(pack.path)

    surface = distill_workspace(tmp_path, pack, timezone="Europe/Amsterdam")
    fields = {field.name: field for field in surface.forms[0].fields}

    assert fields["date"].type == "datetime"
    assert fields["date"].default == datetime.now(ZoneInfo("Europe/Amsterdam")).date().isoformat()


def test_workspace_builds_current_balance_and_low_stock_views(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    (pack.path / "FUNCTIONALITY.md").write_text(
        "## Purpose\n\nTrack stock.\n\n## Services\n\n- Record a change.\n\n"
        "## Interface\n\nCapture:\n- item (required)\n- quantity (decimal)\n"
        "- replenishment (decimal)\n\n"
        "Show:\n- current balance of quantity by item\n"
        "- low stock of quantity below replenishment\n",
        encoding="utf-8",
    )
    pack = AppPack.load(pack.path)
    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put("inbox", {"item": "eggs", "quantity": "6", "replenishment": "12"})
    store.put("inbox", {"item": "eggs", "quantity": "-2"})
    store.put("inbox", {"item": "milk", "quantity": "4", "replenishment": "2"})
    store.close()

    surface = distill_workspace(tmp_path, pack)
    tables = {
        block.title: block
        for block in (surface.view.blocks if surface.view else [])
        if getattr(block, "type", None) == "table"
    }

    assert "Current balance" in tables
    egg = next(row for row in tables["Current balance"].rows if row[0] == "eggs")
    assert egg[1] == "4"
    assert "Low stock" in tables
    assert any(row[0] == "eggs" for row in tables["Low stock"].rows)
    assert all(row[0] != "milk" for row in tables["Low stock"].rows)


def test_workspace_open_only_hides_completed_rows(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    (pack.path / "FUNCTIONALITY.md").write_text(
        "## Purpose\n\nTrack work.\n\n## Services\n\n- Record a task.\n\n"
        "## Interface\n\nCapture:\n- description (required)\n- status\n\n"
        "Show:\n- records\n- open only\n",
        encoding="utf-8",
    )
    pack = AppPack.load(pack.path)
    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put("inbox", {"description": "call", "status": "open"})
    store.put("inbox", {"description": "done job", "status": "done"})
    store.close()

    surface = distill_workspace(tmp_path, pack)
    table = next(
        block
        for block in (surface.view.blocks if surface.view else [])
        if getattr(block, "type", None) == "table"
    )

    assert surface.filters.get("open_only") is True
    assert table.title == "Open records"
    assert any("call" in row for row in table.rows)
    assert all("done job" not in row for row in table.rows)


def test_workspace_is_stateless_when_the_app_keeps_no_documents(tmp_path: Path) -> None:
    pack = _pack(tmp_path, capabilities=("calculation",))
    surface = distill_workspace(tmp_path, pack)

    assert surface.source == "stateless"
    assert surface.forms == []
    assert surface.view is not None


def test_workspace_distills_forms_and_tables_from_observed_records(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path)
    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put("inbox", {"label": "alpha", "count": "2"}, document_id="one")
    store.put("inbox", {"label": "beta", "count": "5"}, document_id="two")
    store.close()

    surface = distill_workspace(tmp_path, pack)

    assert surface.source == "observed"
    assert surface.forms[0].collection == "inbox"
    assert surface.forms[0].mode == "open"
    names = {field.name for field in surface.forms[0].fields}
    assert {"label", "count"} <= names
    assert "recorded_at" not in names
    assert surface.view is not None
    assert any(block.type == "table" for block in surface.view.blocks)
    assert any(block.type == "metric" and block.label == "count" for block in surface.view.blocks)
    assert len(surface.records) == 2
    assert "alpha" in surface.records[1]["title"] or "alpha" in surface.records[0]["title"]


def test_workspace_capture_writes_a_document_without_conversation(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path)
    recorded = capture_document(
        tmp_path,
        pack,
        collection="inbox",
        document={"label": "gamma"},
    )
    surface = distill_workspace(tmp_path, pack)

    assert recorded["document"] == {"label": "gamma"}
    assert any(item["document"]["label"] == "gamma" for item in surface.records)


def test_workspace_skips_stamps_offers_choices_and_merges_updates(
    tmp_path: Path,
) -> None:
    pack = _pack(tmp_path)
    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put(
        "inbox",
        {"label": "alpha", "status": "open", "recorded_at": "2026-09-11T10:00:00+00:00"},
        document_id="one",
    )
    store.put(
        "inbox",
        {"label": "beta", "status": "open", "recorded_at": "2026-09-12T10:00:00+00:00"},
        document_id="two",
    )
    store.put(
        "inbox",
        {"label": "gamma", "status": "done", "recorded_at": "2026-09-13T10:00:00+00:00"},
        document_id="three",
    )
    store.close()

    surface = distill_workspace(tmp_path, pack)
    fields = {field.name: field for field in surface.forms[0].fields}
    assert "recorded_at" not in fields
    assert fields["status"].options == ["done", "open"]

    updated = capture_document(
        tmp_path,
        pack,
        collection="inbox",
        document={"status": "done"},
        document_id="one",
    )
    assert updated["document"]["label"] == "alpha"
    assert updated["document"]["status"] == "done"
    assert "recorded_at" in updated["document"]


def test_workspace_hides_agent_tags_and_groups_numeric_totals(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    database = tmp_path / "var" / "prompt-os.sqlite"
    database.parent.mkdir(parents=True)
    store = DocumentStore(database, app_id=pack.id)
    store.put(
        "inbox",
        {
            "label": "alpha",
            "kind": "food",
            "count": "10",
            "type": "item",
            "review_status": "suggested",
            "timezone": "UTC",
        },
        document_id="one",
    )
    store.put(
        "inbox",
        {
            "label": "beta",
            "kind": "fuel",
            "count": "4",
            "type": "item",
            "review_status": "accepted",
            "timezone": "UTC",
        },
        document_id="two",
    )
    store.put(
        "inbox",
        {
            "label": "gamma",
            "kind": "food",
            "count": "6",
            "type": "item",
            "review_status": "accepted",
            "timezone": "UTC",
        },
        document_id="three",
    )
    store.close()

    surface = distill_workspace(tmp_path, pack)
    fields = {field.name: field for field in surface.forms[0].fields}
    assert "type" not in fields
    assert "review_status" not in fields
    assert "timezone" not in fields
    assert surface.forms[0].defaults["type"] == "item"
    assert fields["kind"].options == ["food", "fuel"]
    assert any(block.type == "bar-chart" for block in (surface.view.blocks if surface.view else []))
