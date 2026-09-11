import pytest
from pydantic import ValidationError

from prompt_os.view_description import ViewDescription


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
