# Engineering constitution

This repository implements a generic application harness.

- Keep product decisions in `apps/*/FUNCTIONALITY.md`.
- Write product functionality as business rules and observable outcomes. Avoid persona prompts, model instructions and prompt-engineering terminology.
- Keep `src/prompt_os/` free of application names, fields and workflows.
- Persistence uses schema-neutral JSON JSON documents. Application packs may evolve their own record structures.
- Do not hand-author a business data contract for an application. Generate candidates from its functionality, inbox records, corrections and failed queries; replay them before explicit promotion.
- MCP tools provide fundamental capabilities such as storage, clocks and deterministic aggregation. They must remain independent of any one application.
- Arithmetic, clock resolution and stored-data aggregates use deterministic services rather than generated totals.
- Never overwrite a production application specification through a live conversation. Changes go through candidate, replay and explicit promotion.
- Do not add a UI-specific business workflow. Clients render generic view descriptions.
- Tests should assert business outcomes, not exact generated wording.
