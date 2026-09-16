# Time and activity log

## Purpose

The service records how time was spent and produces small, trustworthy summaries for a day, week or month.

## Services

- Record one or more activities from a natural description.
- Associate an activity with a project, client or other grouping the user already uses.
- Correct previously recorded time without losing the correction history.
- Show detailed activity and grouped totals for a requested period.
- Learn useful record structure from recurring language, subject to explicit promotion.

## Operating rules

- Duration, start time and project are never invented.
- Relative dates use the service clock and configured timezone.
- Ambiguity that changes the recorded amount, date or owner is resolved before committing the record.
- A correction updates or supersedes the intended record rather than silently adding a duplicate.
- Totals come from the aggregation service over stored records.
- Early records may remain loosely structured until a proposed structure has been reviewed and promoted.

## Interface

Capture:
- description (required, text)
- duration (decimal)
- start
- project
- date

Show:
- records
- total of duration
- duration by project

## Acceptance examples

- “Yesterday I spent 2h on Acme and 45m in stand-up” records two activities on the correct local date.
- “Actually Acme was 90 minutes” corrects the intended entry and preserves the change history.
- “What did I do this week?” reflects stored activities and calculated totals only.

