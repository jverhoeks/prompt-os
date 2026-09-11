import json
from pathlib import Path

from prompt_os.tracing import TraceWriter


def test_trace_is_one_append_only_json_record_per_turn(tmp_path: Path) -> None:
    path = tmp_path / "traces" / "sample-app.jsonl"
    writer = TraceWriter(path)
    first = writer.write(
        app_id="sample-app",
        model="test-model",
        user_message="remember this",
        started_at="2026-09-10T12:00:00+00:00",
        outcome="completed",
        reply="Saved.",
        tool_calls=[{"name": "store.put", "arguments": {"document": {"raw": "remember this"}}}],
    )
    second = writer.write(
        app_id="sample-app",
        model="test-model",
        user_message="show it",
        started_at="2026-09-10T12:01:00+00:00",
        outcome="completed",
        reply="Here it is.",
    )

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records == [first, second]
    assert records[0]["tool_calls"][0]["name"] == "store.put"
    assert "api_key" not in records[0]

