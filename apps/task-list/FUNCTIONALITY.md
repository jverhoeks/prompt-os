# Task list

## Purpose

The service keeps a lightweight record of commitments and helps the user understand what is open, due or completed.

## Services

- Record a task from a short description.
- Add a due date, priority, context or note when the user supplies one.
- Show open work for a requested period or context.
- Mark an existing task complete and retain its completion history.
- Archive unwanted tasks without losing their audit history.

## Operating rules

- A new task requires a meaningful description; all other details are optional.
- Relative dates use the user’s configured timezone and the service clock.
- A reference such as “that task” is accepted only when one recent task is unambiguous.
- Completion changes the existing task rather than creating a second task.
- A destructive or bulk archive requires explicit confirmation.
- Priority is not inferred merely from emotional wording.

## Acceptance examples

- “Call Alex tomorrow” creates one open task with tomorrow’s local date.
- “Mark the invoice task done” completes a unique matching task and reports which one changed.
- “What is due this week?” reports stored tasks only.

