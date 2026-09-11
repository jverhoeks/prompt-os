# Expense log

## Purpose

The service records personal expenses and provides transparent summaries by period and classification.

## Services

- Record an amount, currency, date, merchant or description.
- Suggest a classification from prior accepted examples while allowing correction.
- Attach optional notes, payment method and reimbursable status.
- Report individual expenses and grouped totals for a requested period.
- Correct or archive a record while retaining an audit trail.

## Operating rules

- An expense is not recorded without an amount and currency.
- Decimal precision from the original amount is preserved.
- Relative dates use the configured timezone and service clock.
- A suggested classification is identified as a suggestion until accepted through use or correction.
- Totals come from the aggregation service and keep currencies separate.
- Cross-currency totals require dated exchange rates and disclose their source.

## Acceptance examples

- “Lunch was €18.50” records exactly EUR 18.50 on the applicable date.
- “That was client work” updates the relevant expense as reimbursable when the reference is clear.
- “Spending by category this month” uses stored expenses and deterministic grouped totals.

