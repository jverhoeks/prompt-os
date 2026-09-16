# Calculator and converter

## Purpose

The service answers everyday calculation and measurement-conversion questions with results that can be checked and repeated.

## Services

- Evaluate arithmetic expressions, percentages, simple comparisons and elementary functions such as sine, cosine, square root and logarithms.
- Convert compatible measurements such as distance, weight, temperature, area and volume.
- Plot a continuous function of one variable over a requested interval using sampled values from the calculation service.
- Explain the calculation when an explanation helps the user verify the answer.
- Retain a calculation in history only when history is enabled for the user.

## Operating rules

- The displayed result comes from the calculation service, not an estimate.
- A plotted curve is drawn from calculation-service samples of the stated expression, not from a freehand sketch.
- If the plot interval is not given, use an interval that shows the characteristic shape: one period for a periodic function, otherwise a neighbourhood of zero.
- The original values, operation and units remain visible in the result.
- Incompatible units are not converted.
- Ambiguous notation is clarified before calculation when it could materially change the answer.
- Currency conversion is unavailable until a dated exchange-rate source is configured.
- Rounding is disclosed whenever the displayed value differs from the computed value.

## Acceptance examples

- “What is 17% of 84?” returns the computed value and the expression used.
- “Convert 3 miles to kilometres” returns both quantities and the conversion basis.
- “Convert 10 metres to kilograms” explains that the units describe different kinds of measurement.
- “Plot y = sin(x) from −π to π” shows a continuous curve of sampled values of that expression.

