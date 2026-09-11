# Prompt OS — working design

Status: implementation started on 2026-09-10.

## Product idea

Prompt OS runs small applications whose changing product behaviour is maintained as English business specifications. Code supplies reusable infrastructure: conversations, clocks, identifiers, document storage, tool dispatch, traces, version management and presentation adapters.

An application pack describes the service from a business perspective. It does not describe how an AI should behave, which prompt technique to use, or how the runtime should call tools. A business owner should be able to review the functionality without knowing how the harness works.

## Boundaries


- Application-specific rules do not belong in the harness.
- An application receives only the fundamental tools named by its declared capabilities, plus generic contract-lifecycle and presentation tools.
- Application packs do not contain SQL or provider-specific API instructions.
- The document store accepts JSON and does not impose a shared business schema.
- Each application starts without a business data contract. Early records enter a loose `inbox` collection.
- Data contracts are generated from the business functionality, observed records and user corrections. Generated contracts are candidates until replayed and explicitly promoted.
- The harness understands only the generic contract format. Collection names, field names, relationships and validation choices are generated application artifacts.
- Important calculations and aggregates are performed by deterministic services.
- Proposed changes to an application pack are versioned, tested and explicitly promoted.
- User interfaces consume generic view descriptions; they do not recreate business logic.

## Generic presentation

Applications may describe a result using generic text, metric, list, table and bar-chart blocks. The runtime does not prescribe which blocks an application uses. The model selects the smallest useful view from the business functionality, the user request and deterministic tool results; clients decide how to render the same description.

The view vocabulary contains no application fields, categories or workflows. A terminal client may render a table as a native grid while a browser client may render the same block as HTML. Plain textual replies remain available for clients that do not consume structured views.

## Test application ladder

| Application | Primary harness capability under test |
|---|---|
| Calculator and converter | Deterministic service calls and stateless results |
| Task list | Document creation, updates, references and archiving |
| Time and activity log | Relative dates, ambiguity, corrections and aggregates |
| Pantry and inventory | Repeated state changes, quantities, units and aliases |
| Expense log | Precision, classification, currencies and grouped totals |
| Personal library | Entity lifecycle, optional information and preferences |
| Decision journal | Shapeless capture, semantic retrieval and later structure |

## Implementation sequence

1. Business specifications and pack validation.
2. Generic document store and its contracts.
3. Strands model-driven conversation loop with a LiteLLM provider and native MCP tools.
4. Trace capture and semantic acceptance cases.
5. Candidate, replay and human promotion workflow.
6. Generic calculation, time and aggregation services.
7. CLI presentation, followed later by TUI and browser clients.

## Generated data-contract lifecycle

```text
Business functionality
  + inbox records
  + corrections and failed queries
        ↓
generated candidate contract
        ↓
structural validation and acceptance replay
        ↓
explicit promotion
        ↓
current contract used by storage and reporting
```

There is no initial business schema in an app pack. Before the first promotion, `store.put` accepts loose JSON in `inbox`. The contract-generation process proposes a small structure only after it has evidence. Later proposals are based on the current version and describe why a change is useful. Production history is immutable so a previous contract can be restored.

## Fundamental tool blocks

The first shared tool server exposes:

- Clock: `system.now`
- Storage: `store.put`, `store.get`, `store.query`, `store.scan`, `store.archive`
- Structure discovery: `store.describe`
- Deterministic reporting: `store.aggregate`
- Contract lifecycle: `contract.current`, `contract.propose`

Promotion is intentionally not available to the live application agent. It is an explicit operator action. Calculation, unit conversion and semantic retrieval will be added as independent fundamental services rather than application-specific code.

Trace-based functionality improvements follow the same boundary. Generation and replay first create a reviewable candidate. The operator sees the proposed business-specification diff and replay outcome, then explicitly accepts or declines it. Acceptance archives the previous manifest and functionality, promotes the candidate and increments the patch version; a live conversation cannot perform this action.

## Runtime library

The live application loop uses the Strands Agents Python SDK. Strands owns conversation/tool iteration, MCP client integration and model-provider adaptation. LiteLLM remains the configured model gateway. Prompt OS owns the business functionality, generated-contract lifecycle, storage semantics, traces and acceptance grading.

This boundary prevents the harness from growing a second agent framework while keeping all application-specific decisions outside Strands configuration and Python control flow.

## Tracing

Every application turn produces one append-only JSONL record containing the application, model, input, tool activity, reply, timing and outcome. Traces are the raw material for finding repeated failures and proposing improvements. The first version uses local files only and never records model credentials.
