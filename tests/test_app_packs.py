from pathlib import Path
import json

import pytest

from prompt_os.app_pack import AppPack, discover_app_packs


ROOT = Path(__file__).parents[1]


def test_all_initial_app_packs_are_discoverable() -> None:
    packs = discover_app_packs(ROOT / "apps")
    assert [pack.id for pack in packs] == [
        "calculator",
        "task-list",
        "activity-log",
        "inventory",
        "expense-log",
        "personal-library",
        "decision-journal",
    ]


def test_functionality_is_written_as_business_specification() -> None:
    packs = discover_app_packs(ROOT / "apps")
    for pack in packs:
        assert "## Purpose" in pack.functionality
        assert "## Services" in pack.functionality
        assert "## Operating rules" in pack.functionality
        assert "## Acceptance examples" in pack.functionality


def test_harness_source_does_not_name_app_domains() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "prompt_os").glob("*.py")
    ).lower()
    forbidden = ("activity-log", "expense-log", "personal-library", "decision-journal")
    assert not any(term in source for term in forbidden)


def test_manifest_types_are_strict(tmp_path: Path) -> None:
    app_root = tmp_path / "sample"
    app_root.mkdir()
    (app_root / "FUNCTIONALITY.md").write_text("# Purpose\n", encoding="utf-8")
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "id": "sample",
                "name": 42,
                "version": "0.1.0",
                "functionality": "FUNCTIONALITY.md",
                "capabilities": "documents",
                "data_policy": {"unexpected": True},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        AppPack.load(app_root)


def test_data_policy_selects_an_enforced_trace_mode() -> None:
    packs = {pack.id: pack for pack in discover_app_packs(ROOT / "apps")}

    assert packs["task-list"].trace_mode == "full"
    assert packs["expense-log"].trace_mode == "metadata"
    assert packs["calculator"].trace_mode == "off"
