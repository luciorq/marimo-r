# R Language Support — Maintainer Guide

## Overview

marimo supports R cells via `marimo.r(code, ...)`: a Python cell that sends R
source to a persistent R subprocess and renders the result (console output,
plots, or data) back into the notebook. R cells are edited as raw R source in
the browser (backed by a CodeMirror language mode, optional LSP integration,
and a plot-settings panel), and are code-generated into
`_r_output = mo.r("""...""", ...)` Python cells under the hood so the rest of
the runtime (dependency graph, reactivity, execution) treats them like any
other cell.

R support was added on the `r-support` branch as three commits on top of
upstream `marimo-team/marimo`:

- `feat: R language support for marimo notebooks`
- `test(r): add tests for R cells`
- `fix(codegen): codegen strp-types flag`

This doc explains the architecture, lists every file R support touches, and
gives a step-by-step playbook for rebasing onto newer upstream `main`
releases.

## Architecture

### R execution (`marimo/_r/`)

- **`r.py`** — the public `mo.r(code, ...)` API. Encodes Python inputs
  (including Arrow-convertible DataFrames/DuckDB relations) into JSON-safe
  payloads sent to the R subprocess; payloads above a 1&nbsp;MB threshold are
  written to a temp file instead of base64-inlined to avoid memory blowup.
  Decodes R responses (stdout/stderr, plots, values/Arrow tables) back into
  Python and renders them via `mo.output.replace`. Also exposes
  `reset_session()` to restart the R subprocess.
- **`session.py`** — implements `RSession`, a wrapper around a persistent
  `R --vanilla --slave` subprocess communicating via line-delimited JSON over
  stdin/stdout. `execute()` builds a request (code, inputs, capture/plot
  flags), writes it to the process, and parses the JSON response into an
  `RResponse` dataclass. It registers the subprocess on the kernel's
  execution context so `SIGINT` can be forwarded to it (see
  `marimo/_runtime/handlers.py`), and resets the session on
  `KeyboardInterrupt` to avoid out-of-sync stdin/stdout state.
  `get_session()`/`reset_session()` manage one `RSession` per kernel
  execution context (falling back to a module-level global session outside a
  context, e.g. in tests).
- **`install.py`** — the registration inventory: every point where R support
  attaches to the rest of marimo, each invoked from a one-line, greppable
  `# marimo-r hook` at the bottom of the upstream module it extends. Its
  docstring also lists the direct edits that *cannot* become registrations
  (closed `Literal`/union types, msgspec struct fields, the frontend) and why —
  it is the coupling surface a future plugin or upstream language-API would
  have to cover. `grep -rn "marimo-r hook" marimo/` finds every hook.
- **`lsp_servers.py`** — `RLanguageServer` and `RJarlServer`, registered into
  `CompositeLspServer.LANGUAGE_SERVERS` via `install.py`. They plug in through
  two generic mechanisms (`REQUIRES_CONFIG_READER`, `is_enabled_in(config)`)
  rather than name-specific branches in `_server/lsp.py`, which is now a +33
  line diff instead of +279.
- **`formatting.py`** — `AirFormatter`, `StylerFormatter`, `DefaultRFormatter`,
  moved out of `_utils/formatter.py`, which is byte-identical to upstream again.
- **`launcher.py`** — decides *which* R to run and *in what environment*.
  Resolution order is `MARIMO_R_BINARY`, then this workspace's pixi `r`
  environment, then `R` on `PATH`. See "The pixi R environment" below.
- **`interop.py`** — lazy-import helpers for converting between Arrow,
  DuckDB, and Polars representations, bridging R's Arrow-based data exchange
  with Python's dataframe ecosystem: `bytes_to_arrow_table`,
  `arrow_to_duckdb_relation`, `arrow_to_polars`, `duckdb_relation_to_arrow`.
  Each uses `DependencyManager.require_many` for clear errors when an
  optional dependency is missing.
- **`resources/r_backend.R`** — the standalone R script run as the
  long-lived subprocess. Implements a simple JSON-RPC-like loop over
  stdin/stdout: read one JSON request per line, decode inputs (JSON values or
  Arrow IPC data, optionally registered into an in-process DuckDB
  connection), evaluate the R code with stdout/message/warning capture,
  optionally capture a plotting device's output as PNG/SVG, encode the
  resulting value (data frames/Arrow objects → Arrow IPC, inlined as base64
  or written to a temp file above a size threshold), and write one JSON
  response line back. Runs until stdin closes; cleans up the DuckDB
  connection on exit.

### The pixi R environment

R and its packages are pinned in `pixi.lock` like every other dependency. They
live in a dedicated feature so the R environment can be declared with
`no-default-feature`. The JS toolchain is split out the same way, into a `node`
feature, so each CI job installs only the toolchain it uses:

```toml
[tool.pixi.feature.r.dependencies]   # r-base, r-jsonlite, r-arrow, r-dbi, ...
[tool.pixi.environments]
r = {features = ["r"], no-default-feature = true}
```

The `r` environment contains **no python, node, or uv**, and does not include
the editable marimo install. marimo talks to R over a subprocess pipe, so the
two runtimes only need to agree on Arrow as a wire format — not share an
environment. Keeping them separate also means `pixi install` for ordinary Python
work never solves or downloads the R toolchain.

The feature also carries the R language servers, so all three backends work
without a separate install:

| Tool | conda-forge package | Role |
| --- | --- | --- |
| `languageserver` | `r-languageserver` + `r-lintr`, `r-styler` | the LSP backend: completions, hover, diagnostics |
| `jarl` | `jarl` ([etiennebacher/jarl](https://github.com/etiennebacher/jarl)) | opt-in second LSP server: lint diagnostics |
| `air` | `air` ([posit-dev/air](https://github.com/posit-dev/air)) | **formats R cells** — not a language server here |

`language_servers.r.backend` is `auto` or `languageserver`; `auto` is currently
equivalent. `lintr` and `styler` are not optional: `languageserver` shells out
to them, and without them it reports no diagnostics and formats nothing.

**Air is not an LSP backend, deliberately.** Its language server advertises
formatting and nothing else, and marimo never sends `textDocument/formatting` —
grep the frontend, there are no call sites. R cells are formatted by
`marimo/_utils/formatter.py`, which routes R to `AirFormatter` and runs
`air format` on a temp file, via marimo's own `/format` endpoint. Air was
briefly selectable as a backend, which started a healthy-looking server that
answered no request marimo makes; a legacy `backend = "air"` now reads as
`languageserver`. Air still does all R formatting, independent of any LSP
setting — so a missing `air` breaks formatting, not the language server.

**jarl is not one of those backends.** It is a linter that speaks LSP —
diagnostics and quick fixes only, no completions or hover, and it rejects pull
diagnostics outright. Making it a third mutually-exclusive choice would trade
completions away for lint rules. Instead it runs as a *second* server alongside
whichever backend is selected, and the frontend merges the two with
`FederatedLanguageServerClient` — the same mechanism that runs several Python
type checkers at once:

```toml
[language_servers.r]
enabled = true
backend = "languageserver"   # completions, hover

[language_servers.r.jarl]
enabled = true               # plus jarl's lint diagnostics
```

Two things this required, both worth knowing before changing that code:

- **jarl publishes diagnostics only in response to `textDocument/didSave`.**
  Not on open, not on change; pull diagnostics return
  `-32601 "this is a diagnostics-only LSP server"`. Notebook cells are never
  saved as files, and nothing in marimo called `textDocumentDidSave` — the
  method existed with zero call sites. `NotebookLanguageServerClient` now sends
  one after R edits settle (debounced), which is the only reason jarl produces
  anything at all here.
- **The two servers need separate ports**, so jarl is registered in
  `CompositeLspServer.LANGUAGE_SERVERS` as `r_jarl` rather than sharing the `r`
  entry. Its config lives nested under `r` because it augments R support, so
  `_is_enabled` special-cases it.

```bash
pixi install -e r          # install just the R toolchain
pixi run -e r r-repl       # an R REPL in the pinned library
pixi run -e r r-version    # print R's version and .libPaths()
pixi run test_r            # run tests/_r against the pixi R
pixi run check_r_isolation # print the R subprocess's .libPaths()
```

Two implementation notes that are easy to get wrong:

- **marimo execs the environment's R binary directly; it does not shell out to
  `pixi run -e r R`.** `marimo/_runtime/handlers.py` interrupts a running R cell
  with `r_proc.send_signal(SIGINT)` against the direct child PID. With `pixi run`
  in between, the signal would reach pixi rather than R and the cell would not
  interrupt. `launcher.py` resolves `.pixi/envs/r/bin/R` itself (falling back to
  `pixi info --json` when `detached-environments` moves it), which is the same
  binary with a flat process tree.

- **`R --vanilla` does not isolate the library path.** It stops R reading
  `~/.Renviron` and `~/.Rprofile`, but `R_LIBS`, `R_LIBS_USER`, and
  `R_LIBS_SITE` are ordinary environment variables that survive it — and R
  prepends them *ahead* of the environment's own library, so a stray package in
  `~/R/x86_64-pc-linux-gnu-library/4.5` silently shadows the version pinned in
  `pixi.lock`. `build_environment()` clears all of them plus `R_HOME` and the
  `R_ENVIRON*`/`R_PROFILE*` variables, then pins `R_LIBS_USER` and
  `R_LIBS_SITE` to the pixi library. Pinning matters as much as clearing:
  with `R_LIBS_USER` merely unset, R falls back to its per-user default path.
  `r_backend.R` then re-applies the same list with `.libPaths()` as a second
  line of defence.

  This sanitizing applies **only** when the R binary belongs to an environment
  we manage. For a system R found on `PATH`, the user's `R_LIBS_USER` is where
  their packages actually live, so it is left untouched.

- **The language server needs the same treatment, and gets it.** The
  `languageserver` backend really does start an R process — `packages/lsp/index.ts`
  runs `R --vanilla -e languageserver::run()` — which inherits marimo's
  environment through the node shim. `RLanguageServer.get_environment()`
  applies the same policy as `launcher.py`, so the language server resolves
  packages against the same library that R cells execute in. Without it you get
  the worst kind of bug: completions and diagnostics for packages that are not
  installed where the code actually runs, and none for packages that are.

  `RLanguageServer` also resolves `R`, `air`, and `jarl` through
  `find_r_tool()` rather than `which`, because the marimo process runs in the
  `default` environment and its `PATH` does not include `.pixi/envs/r/bin`.
  Before this, R cells and the R LSP could use two different R installations on
  the same machine.

`MARIMO_R_BINARY` overrides discovery entirely; `MARIMO_R_USE_PIXI=0` skips the
pixi lookup; `MARIMO_R_PIXI_ENV` renames the environment to look for.

### Runtime integration

- **`marimo/_runtime/handlers.py`** — the SIGINT handler forwards the signal
  to the registered R subprocess (via `ExecutionContext.with_r_process`)
  instead of only raising `MarimoInterrupt` in-process, so a running R
  computation is actually interrupted.
- **`marimo/_runtime/context/types.py`** — `ExecutionContext` gained
  `with_r_process(proc)` / a way to track the currently-running R subprocess
  for a cell, used by the interrupt handler above.
- **`marimo/_runtime/commands.py`, `marimo/_runtime/kernel_request_handlers.py`**
  — dispatch the `ResetRSessionCommand` control command (see below) to
  `marimo._r.session.reset_session()`.
- **`marimo/_messaging/notification.py`** — notification types used to
  surface R-subprocess-related messages (e.g. missing R/backend) to the
  frontend.

### Server integration

- **`marimo/_r/lsp_servers.py`** — `RLanguageServer` (id `"r"`) and
  `RJarlServer` (id `"r_jarl"`), both `BaseLspServer` subclasses, registered
  into `CompositeLspServer.LANGUAGE_SERVERS` via `marimo/_r/install.py`. The
  backend is the R `languageserver` package (`auto` is equivalent; a legacy
  `backend = "air"` is normalized to it — air formats R cells but is not a
  language server, see `marimo/_r/formatting.py`). `validate_requirements()`
  probes for `node`, `R`, and the `languageserver` package — using the same
  sanitized environment the server will run with; `get_command()` builds the
  `node ... index.cjs --lsp languageserver:<R path>` command; `start()` skips
  silently when Node or R is absent. `marimo/_server/lsp.py` itself keeps only
  the generic mechanisms these plug into (`REQUIRES_CONFIG_READER`,
  `is_enabled_in(config)`, `get_environment()`) plus the one-line hook import.
- **`marimo/_server/api/endpoints/execution.py`** — defines
  `POST /reset_r_session` (requires "edit" permission), which dispatches a
  `ResetRSessionRequest` control command to the kernel, ultimately calling
  `marimo._r.session.reset_session()` to restart the R subprocess for the
  current session.
- **`marimo/_server/models/models.py`** — `ResetRSessionCommand` /
  `ResetRSessionRequest` model definitions used by the endpoint above.
- **`marimo/_config/config.py`** — `LanguageServersConfig` gains an
  `r: RLanguageServerConfig` field alongside `pylsp`, `basedpyright`, `ty`,
  `pyrefly`. `RLanguageServerConfig` is a `TypedDict` with `enabled: bool`
  and `backend: Literal["auto", "languageserver"]` (a stored `"air"` is
  accepted and normalized away), mirroring the
  frontend schema and driving `RLanguageServer`'s backend-selection logic.

### Frontend: codemirror language mode

- **`frontend/src/core/codemirror/language/languages/r.ts`** — defines
  `RLanguageAdapter`, the CodeMirror language adapter for R cells.
  `transformIn`/`transformOut` convert between the raw R source shown in the
  editor and the Python-wrapped
  `mo.r("""...""", inputs=..., plot_format=..., ...)` cell source, parsing
  and stripping boolean, string, integer, and dict (`inputs={...}`) kwargs
  via regex with brace-depth matching. `getExtension()` wires up either the
  R LSP (via `languageServerWithClient`, using a lazily-created singleton
  `rLspClient`/`NotebookLanguageServerClient`, gated by
  `lspConfig.r.enabled` and the `r_lsp` capability) or a fallback local
  `rCompletionSource`-based autocompletion, alongside a basic
  `StreamLanguage`-based syntax highlighter
  (`@codemirror/legacy-modes/mode/r`).
- **`frontend/src/core/codemirror/language/languages/r-completions.ts`** —
  provides `rCompletionSource`, a static/offline `CompletionSource` used as a
  fallback for R cells when no LSP is configured or available. Built from
  hard-coded arrays of R keywords/control-flow snippets, constants, base R
  functions, and common tidyverse functions, combined via
  `completeFromList`/`snippetCompletion`.
- **`frontend/src/core/codemirror/language/panel/r.tsx`** — implements
  `RPlotSettings`, a popover UI (gear icon) attached to R cells for
  configuring plot rendering: format (PNG/SVG), width, height, and DPI,
  stored in the cell's `RLanguageMetadata`. Highlights the icon when settings
  differ from defaults and provides a "Reset to defaults" action.
- **`frontend/src/core/codemirror/language/extension.ts`,
  `panel/panel.tsx`, `types.ts`, `languages/python.ts`** — register R
  alongside Python/SQL/Markdown as a selectable cell language and route the
  plot-settings panel for R cells.

### Frontend: LSP client

- **`frontend/src/core/codemirror/lsp/notebook-lsp.ts`** — the generic
  `NotebookLanguageServerClient` special-cases `language === "r"` in several
  places:
  1. `resyncAllDocuments()` skips resync if there are no open R cells
     (`hasLanguageCells()`), since the R LSP process may not be running.
  2. `getNotebookCode()` for R reads the *raw* editor text directly from each
     cell's CodeMirror state rather than the Python-wrapped `mo.r(...)` form,
     since the R LSP can't parse Python.
  3. `isRCell(cellId)` determines whether a cell is an R cell by checking its
     `languageAdapterState` (or, for real-time-collaboration docs, the shared
     `languages/{cellId}` Yjs map) equals `"r"`.
  4. `textDocumentDidOpen` uses `isRCell` to filter out non-R cells before
     forwarding `didOpen` notifications, so the R LSP server only ever sees
     R-cell content merged into its document view. When a cell is filtered
     out, the method returns `false` (matching the `Promise<boolean>`
     signature required by `ILanguageServerClient` — do not return the raw
     params object here, see "Known conflict hotspots" below).
- **`frontend/src/core/codemirror/lsp/transports.ts`, `utils.ts`,
  `packages/lsp/index.ts`** — plumbing to start/route a per-language LSP
  transport, including R's.

### Config & API surface

- **`frontend/src/core/config/config-schema.ts`** — under
  `language_servers`, adds an `r` sub-schema
  (`z.object({ enabled: z.boolean().optional(), backend: z.enum(["auto", "air", "languageserver"]).optional() }).prefault({})`),
  consistent with the other language server entries but with the additional
  `backend` selector unique to R.
- **`frontend/src/core/config/capabilities.ts`** — `capabilitiesAtom`
  includes an `r_lsp: false` boolean field, populated from the
  server-reported `Capabilities` and read via `hasCapability("r_lsp")`. This
  flag gates whether `RLanguageAdapter.getExtension()` wires up the real R
  language server versus falling back to static completions.
- **`frontend/src/components/app-config/user-config-form.tsx`** — adds the
  "R Language Server" enable checkbox and backend `<select>` to the settings
  UI. These fields use the `OverriddenFormField` + `IsOverridden override={override}`
  pattern (not the older `IsOverridden userConfig={config} name="..."` API)
  — see "Known conflict hotspots" below.
- **`packages/openapi/api.yaml`, `packages/openapi/src/api.ts`** — OpenAPI
  contract additions for the `/reset_r_session` endpoint and R-related config
  fields.

## File Inventory

Full list of files touched by R support (`git diff --stat` against the
upstream base tag; regenerate with `git diff --stat <upstream-tag> HEAD`
after each rebase — a stale table here misdirects conflict resolution):

```
 .github/workflows/fork-ci.yml                    |   272 +
 .github/workflows/fork-upstream-check.yml        |    91 +
 .gitignore                                       |     6 +
 AGENTS.md                                        |    51 +-
 CONTRIBUTING.md                                  |    38 +-
 FORK.md                                          |   469 +
 MANIFEST.in                                      |     1 +
 README.md                                        |    16 +
 docs/development/r_support.md                    |   591 +
 examples/r/marimo_r_anywidget.py                 |   273 +
 examples/r/marimo_r_example.py                   |   124 +
 examples/r/marimo_r_genomics_interactive.py      |   749 ++
 examples/r/marimo_r_plots_showcase.py            |    72 +
 examples/r/marimo_r_polars_duckdb.py             |   494 +
 examples/r/simple_example.py                     |    98 +
 frontend/src/__mocks__/requests.ts               |     1 +
 .../components/app-config/user-config-form.tsx   |   103 +
 .../editor/actions/useCellActionButton.tsx       |    21 +-
 .../editor/actions/useNotebookActions.tsx        |    12 +-
 .../editor/actions/useRestartKernel.tsx          |     9 +-
 .../components/editor/cell/CreateCellButton.tsx  |    10 +-
 .../src/components/editor/cell/code/icons.tsx    |    14 +
 .../editor/cell/code/language-toggle.tsx         |    15 +-
 .../navigation/__tests__/navigation.test.ts      |    12 +-
 .../components/editor/renderers/cell-array.tsx   |    17 +
 .../src/core/codemirror/__tests__/format.test.ts |   184 +-
 frontend/src/core/codemirror/ai/request.ts       |     2 +-
 .../src/core/codemirror/copilot/extension.ts     |     3 +-
 frontend/src/core/codemirror/format.ts           |    92 +-
 .../core/codemirror/language/LanguageAdapters.ts |     5 +
 .../language/__tests__/r-completions.test.ts     |   176 +
 .../core/codemirror/language/__tests__/r.test.ts |   475 +
 .../src/core/codemirror/language/extension.ts    |     3 +
 .../core/codemirror/language/languages/python.ts |     6 +
 .../language/languages/r-completions.ts          |   845 ++
 .../src/core/codemirror/language/languages/r.ts  |   506 +
 .../src/core/codemirror/language/panel/panel.tsx |   120 +-
 .../src/core/codemirror/language/panel/r.tsx     |   158 +
 frontend/src/core/codemirror/language/types.ts   |     2 +-
 .../lsp/__tests__/notebook-lsp.test.ts           |   569 +-
 frontend/src/core/codemirror/lsp/notebook-lsp.ts |   167 +-
 frontend/src/core/codemirror/lsp/transports.ts   |     9 +-
 frontend/src/core/codemirror/lsp/utils.ts        |    20 +-
 .../core/config/__tests__/config-schema.test.ts  |    12 +
 frontend/src/core/config/capabilities.ts         |     1 +
 frontend/src/core/config/config-schema.ts        |    39 +
 frontend/src/core/islands/bridge.ts              |     1 +
 .../src/core/kernel/__tests__/handlers.test.ts   |     5 +
 frontend/src/core/network/requests-lazy.ts       |     1 +
 frontend/src/core/network/requests-network.ts    |     8 +
 frontend/src/core/network/requests-static.ts     |     1 +
 frontend/src/core/network/requests-toasting.tsx  |     1 +
 frontend/src/core/network/types.ts               |     1 +
 .../src/core/runtime/__tests__/runtime.test.ts   |     8 +
 frontend/src/core/runtime/runtime.ts             |    11 +-
 frontend/src/core/wasm/bridge.ts                 |     2 +-
 .../plugins/impl/code/any-language-editor.tsx    |     1 +
 marimo/__init__.py                               |     2 +
 marimo/_cli/cli.py                               |     9 +-
 marimo/_cli/development/commands.py              |     2 +
 marimo/_cli/sandbox.py                           |    53 +-
 marimo/_config/config.py                         |    44 +-
 marimo/_data/_external_storage/utils.py          |     1 +
 marimo/_dependencies/dependencies.py             |     5 +
 marimo/_messaging/notification.py                |    16 +
 marimo/_r/__init__.py                            |     2 +
 marimo/_r/formatting.py                          |   155 +
 marimo/_r/install.py                             |    70 +
 marimo/_r/interop.py                             |    70 +
 marimo/_r/launcher.py                            |   274 +
 marimo/_r/lsp_servers.py                         |   301 +
 marimo/_r/r.py                                   |   326 +
 marimo/_r/resources/r_backend.R                  |   381 +
 marimo/_r/session.py                             |   247 +
 marimo/_runtime/commands.py                      |     5 +
 marimo/_runtime/context/types.py                 |    11 +
 marimo/_runtime/handlers.py                      |    17 +
 marimo/_runtime/kernel_request_handlers.py       |    10 +
 marimo/_server/api/endpoints/editing.py          |    68 +-
 marimo/_server/api/endpoints/execution.py        |    35 +-
 marimo/_server/api/utils.py                      |    12 +
 marimo/_server/lsp.py                            |    37 +-
 marimo/_server/models/completion.py              |     2 +-
 marimo/_server/models/models.py                  |     9 +
 marimo/_session/capabilities.py                  |     5 +
 marimo/_utils/inline_script_metadata.py          |    18 +-
 marimo/_version.py                               |    12 +-
 packages/llm-info/package.json                   |     4 +-
 packages/lsp/index.test.ts                       |    32 +
 packages/lsp/index.ts                            |    14 +
 packages/openapi/api.yaml                        |    92 +-
 packages/openapi/src/api.ts                      |    98 +-
 pixi.lock                                        | 11782 +++++++++++++++++
 pyproject.toml                                   |   161 +-
 recipe/recipe.yaml                               |   199 +
 scripts/fork/disable-upstream-workflows.sh       |    75 +
 scripts/fork/r_doctor.py                         |   209 +
 scripts/fork/stub-built-assets.sh                |    29 +
 scripts/fork/sync-upstream.sh                    |   141 +
 tests/_cli/test_sandbox.py                       |    41 +-
 tests/_r/test_formatting.py                      |   260 +
 tests/_r/test_launcher.py                        |   241 +
 tests/_r/test_lsp_servers.py                     |   553 +
 tests/_r/test_r_encoding.py                      |   775 ++
 tests/_r/test_r_integration.py                   |   196 +
 tests/_r/test_session.py                         |   898 ++
 tests/_runtime/test_interrupt_handlers.py        |   101 +
 tests/_server/test_lsp.py                        |    29 +-
 tests/snapshots/dependencies.txt                 |     1 +
 .../optional-dependencies-recommended.txt        |     4 +-
 tests/test_version.py                            |    23 +-
 111 files changed, 25006 insertions(+), 144 deletions(-)
```

(Regenerate with `git diff --stat <merge-base>..r-support` — find the
merge-base with `git merge-base r-support origin/main` after checking out
the commit just before the R commits, or more simply
`git merge-base origin/main r-support~3` if the 3 R commits are still at the
tip.)

## Testing

Run before considering any change to R support complete:

```bash
uv run --group test pytest tests/_r/ tests/_server/test_lsp.py tests/_utils/test_formatter.py tests/_runtime/test_interrupt_handlers.py
make py-check

cd frontend
pnpm vitest run \
  src/core/codemirror/language/__tests__/r.test.ts \
  src/core/codemirror/lsp/__tests__/notebook-lsp.test.ts \
  src/core/config/__tests__/config-schema.test.ts \
  src/core/kernel/__tests__/handlers.test.ts \
  src/core/runtime/__tests__/runtime.test.ts

cd ../packages/lsp && pnpm vitest run
cd ../.. && make fe-check
```

**Known environment limitation:** `tests/_r/test_session.py::TestRBackendIntegration::*`
and most of `tests/_r/test_r_integration.py` spawn a real R subprocess and
require R packages (`arrow`, `ggplot2`, `duckdb`, etc.) to be installed and
on `.libPaths()` for whichever R binary is first on `PATH` when Python's
`subprocess` module resolves `Rscript`/`R`. In a pixi-managed environment
with multiple R installations (e.g. one pixi env's R plus a separate
`r-base` env), the subprocess may find an R without the required packages
installed, causing these integration tests to fail with errors like
`there is no package called 'arrow'`. This is a local environment
configuration issue, not a code regression — verify by running the same
test against `git checkout <pre-change-commit>` if in doubt. The rest of
`tests/_r/` (encoding logic, session unit tests that mock the subprocess,
etc.) does not require a real R installation and should always pass.

## Rebase Playbook

When upstream has cut a new release and the R patch series needs to catch up.
Note that in this fork `origin` is `luciorq/marimo-r` and `upstream` is
`marimo-team/marimo` — see [FORK.md](../../FORK.md) for the branch, tag, and
release model.

1. See where you stand and what will be replayed:
   `scripts/fork/sync-upstream.sh check` and `scripts/fork/sync-upstream.sh series`.
2. Start the rebase: `scripts/fork/sync-upstream.sh rebase <tag>`. This refuses
   to run on a dirty tree, creates a `backup/main-<timestamp>` branch, and
   replays only the fork's commits onto the upstream tag.
3. While resolving, fold any previous `fix(r): repair ... after rebase` commits
   into the commits they repair, so the series does not grow one commit per sync.
4. Resolve conflicts by re-porting R logic into upstream's new shape — see
   "Known conflict hotspots" below for patterns seen so far. A merge helper
   (`mergiraf`, if configured as a git mergetool) will auto-resolve many
   mechanical/import-ordering conflicts; real conflicts tend to cluster in
   the files listed below.
5. After each `git rebase --continue`, if commit messages need an editor and
   none is configured, use `GIT_EDITOR=true git rebase --continue` to accept
   the default message non-interactively.
6. Run the full Testing section above. Fix real regressions; don't chase
   pre-existing/environment-only failures (see "Known environment
   limitation" above) — but do verify they're pre-existing by checking the
   same test against the `backup/main-*` branch before assuming so.
7. Update this doc's "Known conflict hotspots" and "File Inventory" sections
   with anything new observed, so the next rebase is easier.

### Known conflict hotspots

- **Upstream registries the fork must opt into — these produce no conflict
  markers.** The rebase onto 0.24.0 applied cleanly, and R cells worked, but
  clicking "Reset R session" raised
  `AssertionError: Command ResetRSessionCommand is not classified into a
  capability tier`. Upstream had added `marimo/_session/capabilities.py`, which
  requires every command in the `CommandMessage` union to be tiered READ /
  INTERACT / EDIT; our command predated it, so nothing conflicted and nothing
  failed until dispatch.

  After any rebase, run upstream's completeness tests — they exist precisely to
  catch this, and they take under a second:

  ```bash
  pixi run uv run --group test pytest \
    tests/_session/test_capabilities.py \
    tests/_runtime/test_request_router.py \
    tests/_utils/test_msgspec_basestruct.py
  ```

  They are in the CI smoke job for the same reason. `ResetRSessionCommand` is
  the fork's only command; if you add another, it must be registered in
  `_runtime/commands.py`, routed in `_runtime/kernel_request_handlers.py`,
  exported in `_cli/development/commands.py`, **and** tiered in
  `_session/capabilities.py`. The first three were already habits; the fourth is
  new as of 0.24.0, and there may be a fifth next release — which is why the
  completeness tests, rather than this list, are the real guard.

As of the 2026-08-05 rebase (231 commits, `e55d04da2` → `deff2a2f8`):

- **`tests/_runtime/test_interrupt_handlers.py`** — conflicted because
  upstream added a new unrelated test
  (`test_ignore_console_ctrl_c_keeps_interrupt_main_working`) right where the
  R commit appended its own `# R process interrupt tests` section. Resolved
  by keeping both — this is an append-only file, so future conflicts here
  are almost always resolvable by concatenating both sides in file order.

- **`frontend/src/core/codemirror/lsp/notebook-lsp.ts`** — this file sees
  real conflicts nearly every rebase, because both upstream and the R commit
  independently touch `resyncAllDocuments()` and `textDocumentDidOpen()`:
  - Upstream refactored `resyncAllDocuments()` to send `textDocument/didOpen`
    directly via `client.notify(...)` (to avoid incrementing the new
    per-cell `openCellDocumentCounts` ref-counting map) instead of calling
    `client.textDocumentDidOpen(...)`. The R commit's contribution here is
    just the `languageId: this.language` value (defaulting to `"r"`/`"python"`/etc.
    instead of a hardcoded `"python"`) — re-apply that one-line change inside
    whichever notify/call upstream uses.
  - Upstream hoisted the `Logger.debug`/`assertCellDocumentUri`/snapshot
    logic in `textDocumentDidOpen` out of the `try` block and switched from a
    static `NotebookLanguageServerClient.SEEN_CELL_DOCUMENT_URIS` Set to an
    instance-level `this.seenCellDocumentUris` Set. The R commit's
    contribution is just the `isRCell` early-return check — re-add it as the
    first statement inside the `try` block, using whatever variables
    (`cellDocumentUri`, `lens`, `version`) are already in scope from the
    hoisted code. **Important:** the early return must be `return false;`
    (matching the `Promise<boolean>` signature `ILanguageServerClient`
    requires), never `return params;` — returning the raw params object
    breaks the type contract and causes downstream type errors in
    `languages/python.ts` and any other consumer of `ILanguageServerClient`.
  - `frontend/src/core/codemirror/lsp/__tests__/notebook-lsp.test.ts` — the
    "R language mode" describe block's `beforeEach` used to call
    `(NotebookLanguageServerClient as any).SEEN_CELL_DOCUMENT_URIS.clear()`.
    Since upstream removed the static Set (see above), this line becomes
    dead/broken code — just delete it; each test creates a fresh
    `NotebookLanguageServerClient` instance with its own
    `seenCellDocumentUris`, so no explicit clearing is needed. Also watch for
    the `rMockClient` object literal drifting out of sync with
    `ILanguageServerClient`'s member list (`hasCapability`,
    `textDocumentDidClose`, `textDocumentWillSave`,
    `textDocumentWillSaveWaitUntil`, `textDocumentDidSave`,
    `codeActionResolve`, etc.) — copy the full field list from the main
    `mockClient` fixture earlier in the same file.

- **`frontend/src/components/app-config/user-config-form.tsx`** — no
  textual conflict markers, but a silent behavioral drift: upstream migrated
  the config-override UI from
  `<IsOverridden userConfig={config} name="some.path" />` (paired with plain
  `<FormField render={({ field }) => ...} />`) to
  `<IsOverridden override={override} />` (paired with
  `<OverriddenFormField render={({ field, override }) => ...} />`, where
  `override` comes from the render prop). Because this file has heavy
  line-level overlap, the merge tool can leave R's newly-added
  `language_servers.r.enabled` / `language_servers.r.backend` fields (and
  even pre-existing fields like `diagnostics.enabled`) using the *old* API
  shape even when the surrounding file has otherwise moved to the new one.
  This only surfaces as a TypeScript error (`Property 'override' does not
  exist...` / `Property 'userConfig' does not exist...`), not a git conflict
  marker — so **always run `make fe-check` (typecheck) after resolving this
  file**, and grep the file for `IsOverridden` /  `FormField` /
  `OverriddenFormField` usages near any R-related or recently-touched block
  to make sure they're all using the current pattern.

- **`frontend/src/core/codemirror/language/__tests__/r.test.ts`** — this
  test file can silently drift out of sync with type-only exports it
  imports inline (e.g. `CompletionConfig` from `@/core/config/config-schema`,
  `HotkeyProvider` from `@/core/hotkeys/hotkeys`) if those types move or if
  the file was originally written assuming a looser object-literal shape for
  `HotkeyProvider` (a class with private fields) — construct it with
  `HotkeyProvider.create()` rather than a hand-rolled object literal.

- **Lint/format-only diffs** — a rebase commonly triggers `ruff`
  auto-formatting/import-sorting fixes (e.g. alphabetizing `ResetRSessionRequest`
  among other imports, wrapping long lines) and missing-copyright-header
  insertions (`make py-check` fixes these automatically) in files the R
  commit touches incidentally, even without an R-specific conflict. These
  are safe to accept as-is; just re-run `make py-check`/`make fe-check`
  after the rebase and commit whatever it auto-fixes.

- **mypy stub errors** (`docutils.writers.html4css1`, `ibis`/`ibis.expr`
  "missing library stubs") appear in `make py-check` output but are
  pre-existing on plain `origin/main` in this environment (missing
  `types-docutils`/`ibis` stub packages) — unrelated to R support, safe to
  ignore unless they start appearing in R-specific files.
