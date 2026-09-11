from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from prompt_os.app_pack import AppPack
from prompt_os.cli import main
from prompt_os.improvement import (
    _metadata_trace,
    format_improvement_proposal,
    promote_improvement,
)


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
                "capabilities": ["storage"],
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


def test_proposal_is_readable_and_reports_a_replay_tie(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    result = _create_candidate(tmp_path, pack)

    rendered = format_improvement_proposal(pack, result)

    assert "Summary: Allow retrieval" in rendered
    assert "Replay: current 1/1; candidate 1/1 (no measured improvement)" in rendered
    assert "-Record things." in rendered
    assert "+Record and retrieve things." in rendered


def test_promotion_archives_previous_spec_and_bumps_patch_version(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    result = _create_candidate(tmp_path, pack)

    promotion = promote_improvement(tmp_path, pack, result)

    promoted = AppPack.load(pack.path)
    archive = pack.path / "versions" / "1.2.3"
    assert promoted.version == "1.2.4"
    assert promoted.functionality == "# Purpose\n\nRecord and retrieve things."
    assert (archive / "FUNCTIONALITY.md").read_text(encoding="utf-8") == (
        "# Purpose\n\nRecord things.\n"
    )
    assert json.loads((archive / "app.json").read_text(encoding="utf-8"))["version"] == "1.2.3"
    assert promotion["status"] == "promoted"
    assert json.loads(
        (Path(result["path"]) / "report.json").read_text(encoding="utf-8")
    )["to_version"] == "1.2.4"


def test_declining_cli_proposal_leaves_production_untouched(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    pack = _create_app(tmp_path)
    result = _create_candidate(tmp_path, pack)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("prompt_os.improvement.improve", lambda *args, **kwargs: result)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")

    assert main(["improve", pack.id]) == 0

    assert AppPack.load(pack.path).version == "1.2.3"
    assert not (pack.path / "versions").exists()
    assert "Not promoted" in capsys.readouterr().out


def test_promotion_rejects_a_baseline_regression(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    result = _create_candidate(tmp_path, pack)
    result["candidate"] = {
        "passed": 0,
        "total": 1,
        "cases": [{"id": "case-1", "passed": False}],
    }
    report_path = Path(result["path"]) / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["candidate"] = result["candidate"]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="regresses"):
        promote_improvement(tmp_path, pack, result)

    assert AppPack.load(pack.path).version == "1.2.3"


def test_sensitive_improvement_evidence_redacts_legacy_full_trace_content() -> None:
    sanitized = _metadata_trace(
        {
            "trace_id": "trace-1",
            "app_id": "sample-app",
            "outcome": "completed",
            "user_message": "private",
            "reply": "also private",
            "tool_calls": [
                {"name": "store.put", "arguments": {"secret": "private"}}
            ],
        }
    )

    assert sanitized["content_redacted"] is True
    assert sanitized["tool_calls"] == [{"name": "store.put"}]
    assert "user_message" not in sanitized
    assert "reply" not in sanitized


def test_promotion_rejects_candidate_content_changed_after_replay(tmp_path: Path) -> None:
    pack = _create_app(tmp_path)
    result = _create_candidate(tmp_path, pack)
    (Path(result["path"]) / "FUNCTIONALITY.md").write_text(
        "# Purpose\n\nTampered.\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="changed after replay"):
        promote_improvement(tmp_path, pack, result)
