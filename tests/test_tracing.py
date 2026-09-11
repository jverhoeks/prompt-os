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
    assert [record["turn_index"] for record in records] == [1, 2]
    assert records[0]["session_id"] == records[1]["session_id"]
    assert "api_key" not in records[0]


def test_metadata_trace_redacts_conversation_and_tool_payloads(tmp_path: Path) -> None:
    path = tmp_path / "metadata.jsonl"
    writer = TraceWriter(path, mode="metadata")
    writer.write(
        app_id="sample-app",
        model="model",
        user_message="private message",
        started_at="2026-09-10T12:00:00+00:00",
        outcome="completed",
        reply="private reply",
        tool_calls=[{"name": "store.put", "arguments": {"secret": "value"}}],
    )

    record = json.loads(path.read_text())
    assert record["content_redacted"] is True
    assert record["tool_calls"] == [{"name": "store.put"}]
    assert "user_message" not in record
    assert "reply" not in record
    assert "secret" not in path.read_text()


def test_off_trace_mode_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "off.jsonl"
    record = TraceWriter(path, mode="off").write(
        app_id="sample-app",
        model="model",
        user_message="private message",
        started_at="2026-09-10T12:00:00+00:00",
        outcome="completed",
    )

    assert record["stored"] is False
    assert not path.exists()
