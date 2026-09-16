from __future__ import annotations

import math

import pytest

from prompt_os.calculate import evaluate, sample
from prompt_os.view_description import ViewDescription


def test_decimal_arithmetic_is_unchanged() -> None:
    assert evaluate("84 * 0.17")["value"] == "14.28"


def test_elementary_functions_are_evaluated_deterministically() -> None:
    assert float(evaluate("sin(0)")["value"]) == 0
    assert float(evaluate("cos(0)")["value"]) == pytest.approx(1)
    assert float(evaluate("sqrt(20)")["value"]) == pytest.approx(math.sqrt(20))


def test_sample_draws_a_sine_period_from_named_bounds() -> None:
    result = sample("sin(x)", start="-pi", end="pi", points=5)

    ys = [point["y"] for point in result["points"]]
    assert result["count"] == 5
    assert ys[0] == pytest.approx(0, abs=1e-10)
    assert ys[1] == pytest.approx(-1)
    assert ys[2] == pytest.approx(0, abs=1e-10)
    assert ys[3] == pytest.approx(1)
    assert ys[4] == pytest.approx(0, abs=1e-10)


def test_sample_and_evaluate_reject_runtime_code() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        evaluate("__import__('os').getcwd()")
    with pytest.raises(ValueError, match="unsupported"):
        sample("__import__('os').getcwd()", start=0, end=1, points=2)


def test_line_chart_view_accepts_sampled_points() -> None:
    sampled = sample("sin(x)", start=0, end=math.pi, points=8)
    view = ViewDescription.model_validate(
        {
            "title": "y = sin(x)",
            "blocks": [
                {
                    "type": "line-chart",
                    "x_label": "x",
                    "y_label": "y",
                    "series": sampled["points"],
                }
            ],
        }
    )

    assert view.blocks[0].type == "line-chart"
    assert len(view.blocks[0].series) == 8
