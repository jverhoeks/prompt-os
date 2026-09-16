# Pantry and inventory

## Purpose

The service maintains a practical view of items on hand and identifies items that may need replenishment.

## Services

- Record purchases, additions, usage, disposal and corrections.
- Report current quantities and recent movements.
- Recognise user-approved alternative names for the same item.
- Maintain an optional replenishment level for frequently used items.
- Produce a low-stock or shopping view from current records.

## Operating rules

- Every stock change records the amount, unit, reason and effective time when known.
- Compatible quantities may be combined after deterministic unit conversion.
- Incompatible or unspecified units remain separate until clarified.
- Stock is not allowed to become negative without confirmation that the previous balance was incomplete.
- Similar names are not merged until identity is sufficiently clear.
- A low-stock statement compares the stored balance with a stored replenishment level.

## Acceptance examples

- “We bought six eggs” increases the egg balance by six.
- “I used two” applies to eggs only when the recent context is unambiguous.
- “What are we low on?” lists only items with a recorded replenishment level and a lower current balance.
