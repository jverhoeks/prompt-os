from __future__ import annotations

import hashlib
import json
from pathlib import Path

from prompt_os.app_pack import AppPack
from prompt_os.cli import main
from prompt_os.improvement import format_improvement_proposal, promote_improvement


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
                "data_policy": "local",
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
        "summary": "Allow retrieval",
        "rationale": "A trace showed retrieval was expected.",
        "evidence_trace_ids": ["trace-1"],
        "baseline": {"passed": 1, "total": 1},
        "candidate": {"passed": 1, "total": 1},
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
