# Prompt OS

Prompt OS is a small application harness for products whose behaviour is described in ordinary business language.

The repository separates three concerns:

- `apps/` describes what each product does, in terms a product owner or user can review.
- `src/prompt_os/` contains reusable runtime plumbing and must not contain app-specific decisions.
- `contracts/` defines stable tool interfaces available to every app.

## Current build slice

Seven app packs are included:

1. Calculator and converter
2. Task list
3. Time and activity log
4. Pantry and inventory
5. Expense log
6. Personal library
7. Decision journal

The current harness can discover and validate these packs and provides a schema-neutral SQLite document store. It does not yet connect a language model or expose an interactive chat loop.
## Try it

```bash
python -m prompt_os apps
python -m prompt_os show activity-log
python -m prompt_os validate
python -m pytest
```

When running directly from a checkout without installation:

```bash
PYTHONPATH=src python -m prompt_os apps
```

## Model-driven evaluations

Point the Strands runtime at a LiteLLM proxy. Keep the key in the environment; the harness does not read it from application packs or write it to traces. Strands connects to the fundamental MCP server for every evaluated case.

```bash
export LITELLM_BASE_URL=http://localhost:4000
export LITELLM_API_KEY=your-virtual-key
export LITELLM_MODEL=your-model-alias

PYTHONPATH=src python -m prompt_os eval
PYTHONPATH=src python -m prompt_os eval --app activity-log
PYTHONPATH=src python -m prompt_os eval --suite contracts
PYTHONPATH=src python -m prompt_os eval --suite all
```

Alternatively, copy `.env.example` to `.env` and fill in the values:

```dotenv
LITELLM_BASE_URL=http://localhost:4000
LITELLM_API_KEY=your-virtual-key
LITELLM_MODEL=your-model-alias
```

The CLI loads `.env` from the current project directory. Values already exported in the shell take precedence. `.env` is excluded from Git.
Before a model-driven command starts, the harness verifies that the configured model is visible to the API key and is a chat model with tool support. Model requests use provider defaults for optional sampling parameters, so deployments that only accept their default temperature are supported.

Start an application chat with persistent SQLite data and append-only local traces:

```bash
prompt-os chat activity-log --timezone Europe/Amsterdam
```

Application data is stored in `var/prompt-os.sqlite`; traces are written to `var/traces/<app>.jsonl`. Both stay local and are excluded from Git.
Application replies are rendered as terminal Markdown. Routine tool-call logs are hidden; add `--debug` to the chat command to show detailed MCP request logs.

Run the same application in the full-screen terminal interface:

```bash
uv run prompt-os tui expense-log --timezone Europe/Amsterdam
```

The TUI is a generic conversation renderer: its title and behaviour come from the selected application pack. The model may describe results using generic metrics, lists, tables and bar charts, which the TUI renders as native terminal components without predefined application fields, categories or workflows. Use `Ctrl+L` to clear the visible conversation, `Ctrl+Q` to quit, or add `--debug` to show tool activity after replies.

Application capabilities also constrain the fundamental tools exposed to the model. For example, an application without the `calculation` capability does not receive the deterministic calculator tool, and the runtime declines requests outside the application's business functionality.

After the app has accumulated useful traces, request one narrow improvement:

```bash
prompt-os improve activity-log
```

The command writes a candidate under `var/improvements/`, runs the app's smoke cases against both current and candidate functionality, and shows a readable proposal with its diff and replay comparison. It then asks whether to promote the candidate. Declining leaves production untouched. Explicit approval archives the current specification under `apps/<app>/versions/<version>/`, installs the candidate, and increments the application's patch version.

Each case uses an isolated temporary SQLite database and contract directory. The default suite is a connectivity and tool-use smoke test. The `contracts` suite verifies that Strands can generate evidence-backed contract candidates from each business specification. Candidates are never promoted by an evaluation run. Richer multi-turn improvement comparisons come next.

Every evaluated turn also writes a single append-only JSONL trace inside its isolated temporary directory. The same small trace format will be used by the interactive client; it records model and tool behaviour but never LiteLLM credentials.
