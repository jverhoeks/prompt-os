from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


UNITS: dict[str, tuple[str, Decimal]] = {
    "millimetre": ("length", Decimal("0.001")),
    "centimetre": ("length", Decimal("0.01")),
    "metre": ("length", Decimal("1")),
    "kilometre": ("length", Decimal("1000")),
    "inch": ("length", Decimal("0.0254")),
    "foot": ("length", Decimal("0.3048")),
    "yard": ("length", Decimal("0.9144")),
    "mile": ("length", Decimal("1609.344")),
    "milligram": ("mass", Decimal("0.001")),
    "gram": ("mass", Decimal("1")),
    "kilogram": ("mass", Decimal("1000")),
    "ounce": ("mass", Decimal("28.349523125")),
    "pound": ("mass", Decimal("453.59237")),
    "millilitre": ("volume", Decimal("0.001")),
    "litre": ("volume", Decimal("1")),
    "teaspoon": ("volume", Decimal("0.00492892159375")),
    "tablespoon": ("volume", Decimal("0.01478676478125")),
    "cup": ("volume", Decimal("0.2365882365")),
    "pint": ("volume", Decimal("0.473176473")),
    "gallon": ("volume", Decimal("3.785411784")),
    "square centimetre": ("area", Decimal("0.0001")),
    "square metre": ("area", Decimal("1")),
    "square kilometre": ("area", Decimal("1000000")),
    "square inch": ("area", Decimal("0.00064516")),
    "square foot": ("area", Decimal("0.09290304")),
    "acre": ("area", Decimal("4046.8564224")),
    "hectare": ("area", Decimal("10000")),
}

ALIASES = {
    "mm": "millimetre",
    "millimeter": "millimetre",
    "millimeters": "millimetre",
    "millimetres": "millimetre",
    "cm": "centimetre",
    "centimeter": "centimetre",
    "centimeters": "centimetre",
    "centimetres": "centimetre",
    "m": "metre",
    "meter": "metre",
    "meters": "metre",
    "metres": "metre",
    "km": "kilometre",
    "kilometer": "kilometre",
    "kilometers": "kilometre",
    "kilometres": "kilometre",
    "in": "inch",
    "inches": "inch",
    "ft": "foot",
    "feet": "foot",
    "yd": "yard",
    "yards": "yard",
    "mi": "mile",
    "miles": "mile",
    "mg": "milligram",
    "milligrams": "milligram",
    "g": "gram",
    "grams": "gram",
    "kg": "kilogram",
    "kilograms": "kilogram",
    "oz": "ounce",
    "ounces": "ounce",
    "lb": "pound",
    "lbs": "pound",
    "pounds": "pound",
    "ml": "millilitre",
    "milliliter": "millilitre",
    "milliliters": "millilitre",
    "millilitres": "millilitre",
    "l": "litre",
    "liter": "litre",
    "liters": "litre",
    "litres": "litre",
    "tsp": "teaspoon",
    "teaspoons": "teaspoon",
    "tbsp": "tablespoon",
    "tablespoons": "tablespoon",
    "cups": "cup",
    "pints": "pint",
    "gallons": "gallon",
    "cm2": "square centimetre",
    "cm²": "square centimetre",
    "m2": "square metre",
    "m²": "square metre",
    "km2": "square kilometre",
    "km²": "square kilometre",
    "in2": "square inch",
    "in²": "square inch",
    "ft2": "square foot",
    "ft²": "square foot",
    "acres": "acre",
    "hectares": "hectare",
    "c": "celsius",
    "°c": "celsius",
    "celsius": "celsius",
    "f": "fahrenheit",
    "°f": "fahrenheit",
    "fahrenheit": "fahrenheit",
    "k": "kelvin",
    "kelvin": "kelvin",
}

for _canonical in UNITS:
    ALIASES[_canonical] = _canonical


def convert(value: str | int | float, from_unit: str, to_unit: str) -> dict[str, Any]:
    amount = _number(value)
    source = _unit(from_unit)
    target = _unit(to_unit)
    with localcontext() as context:
        context.prec = 28
        if source in {"celsius", "fahrenheit", "kelvin"}:
            if target not in {"celsius", "fahrenheit", "kelvin"}:
                raise ValueError("units describe different kinds of measurement")
            result = _from_kelvin(_to_kelvin(amount, source), target)
            basis = "exact temperature conversion via kelvin"
        else:
            source_dimension, source_factor = UNITS[source]
            if target not in UNITS or UNITS[target][0] != source_dimension:
                raise ValueError("units describe different kinds of measurement")
            result = amount * source_factor / UNITS[target][1]
            basis = (
                f"1 {source} = {source_factor} base {source_dimension} units; "
                f"1 {target} = {UNITS[target][1]} base {source_dimension} units"
            )
    return {
        "input": {"value": str(amount), "unit": source},
        "output": {"value": _format(result), "unit": target},
        "basis": basis,
    }


def _number(value: str | int | float) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("conversion value must be a finite number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("conversion value must be a finite number") from exc
    if not result.is_finite():
        raise ValueError("conversion value must be a finite number")
    return result


def _unit(value: str) -> str:
    normalized = " ".join(value.strip().casefold().split())
    try:
        return ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported unit {value!r}") from exc


def _to_kelvin(value: Decimal, unit: str) -> Decimal:
    if unit == "kelvin":
        result = value
    elif unit == "celsius":
        result = value + Decimal("273.15")
    else:
        result = (value - Decimal("32")) * Decimal("5") / Decimal("9") + Decimal("273.15")
    if result < 0:
        raise ValueError("temperature is below absolute zero")
    return result


def _from_kelvin(value: Decimal, unit: str) -> Decimal:
    if unit == "kelvin":
        return value
    if unit == "celsius":
        return value - Decimal("273.15")
    return (value - Decimal("273.15")) * Decimal("9") / Decimal("5") + Decimal("32")


def _format(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized
