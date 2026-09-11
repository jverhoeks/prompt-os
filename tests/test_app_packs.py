from pathlib import Path

from prompt_os.app_pack import discover_app_packs


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

