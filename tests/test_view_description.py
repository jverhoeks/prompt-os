import pytest
from pydantic import ValidationError

from prompt_os.view_description import ViewDescription, view_from_tool_calls


def test_generic_view_accepts_mixed_presentation_blocks() -> None:
    view = ViewDescription.model_validate(
        {
            "title": "Overview",
            "blocks": [
                {"type": "metric", "label": "Total", "value": "42"},
                {"type": "list", "items": ["First", "Second"]},
                {
                    "type": "bar-chart",
                    "series": [{"label": "A", "value": 3, "display": "3 units"}],
                },
            ],
        }
    )

    assert len(view.blocks) == 3


def test_view_uses_the_latest_successful_presentation_call() -> None:
    view = view_from_tool_calls(
        [
            {
                "name": "view.present",
                "status": "success",
                "arguments": {"view": {"blocks": [{"type": "text", "markdown": "old"}]}},
            },
            {
                "name": "view.present",
                "status": "error",
                "arguments": {"view": {"blocks": [{"type": "text", "markdown": "bad"}]}},
            },
            {
                "name": "view.present",
                "status": "success",
                "arguments": {
                    "view": {
                        "title": "Latest",
                        "blocks": [{"type": "metric", "label": "Total", "value": "4"}],
                    }
                },
            },
        ]
    )

    assert view is not None
    assert view.title == "Latest"
    assert view.blocks[0].value == "4"


def test_table_rows_must_match_declared_columns() -> None:
    with pytest.raises(ValidationError, match="match the number of columns"):
        ViewDescription.model_validate(
            {
                "blocks": [
                    {
                        "type": "table",
                        "columns": ["One", "Two"],
                        "rows": [["only one"]],
                    }
                ]
            }
        )
