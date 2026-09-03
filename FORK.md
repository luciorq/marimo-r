# Fork maintenance

This repository is a fork of [marimo](https://github.com/marimo-team/marimo)
maintained at **`luciorq/marimo-r`**. Upstream has declined R support, so the
fork is maintained independently.

This document covers how the fork stays in sync with upstream and how it ships.
For how R support *works* — architecture, file inventory, and the conflict
patterns you will hit while rebasing — see
[`docs/development/r_support.md`](docs/development/r_support.md).

## What this fork changes

Two things, in order of importance.

### 1. R language support — the reason the fork exists

R cells, the `marimo.r(...)` API, R↔Python data interchange over Arrow, and an R
language server, so R and Python cells participate in one reactive notebook.
This is the goal; everything else exists to serve it.

Lives in `marimo/_r/`, with touchpoints in the runtime, server, LSP, and
frontend language modes. `docs/development/r_support.md` is the full map.

### 2. pixi as the development environment manager

A change of a different kind, but a large one: upstream treats pixi as an
optional convenience and does not commit `pixi.lock`. Here, pixi is *the* way
the development environment is defined, and the lockfile is tracked.

This is not tidiness for its own sake. R support means the project now spans
three toolchains — Python, Node, and R — and R is the one users are least likely
to have in a workable state. Conda-forge packages R, `jsonlite`, `arrow`,
`duckdb`, and `ggplot2` for every platform the fork targets, so a single
`pixi install -e r` replaces "install R, then work out how to get these five
packages to build". Pinning all three toolchains in one lockfile is what makes
"works on my machine" and "works in CI" the same claim.

Concretely:

- **Three environments**, each installable on its own:
  `default` (Python + Node dev tooling), `node` (Node and pnpm only), and `r`
  (the R toolchain only). `node` and `r` are declared `no-default-feature`, so
  neither drags in Python.
- **`pixi.lock` is tracked**, un-ignored from upstream's `.gitignore`. It is
  what makes the R version reproducible, and CI installs from it.
- **CI installs only what each job needs** — see "GitHub Actions" below.
- **marimo finds its R through pixi at runtime.** `marimo/_r/launcher.py`
  prefers this workspace's pixi `r` environment over whatever is on `PATH`, and
  isolates it from the user's global R library.

If you are contributing here, `pixi run make check` is the gate, not a bare
`make check` — the latter picks up whatever happens to be on your `PATH`.

## Model in one paragraph

The fork is a **short patch series rebased onto upstream release tags**, not a
long-lived divergent branch. `main` is always exactly `<upstream release tag> +
the R commits`. When upstream cuts a release, the series is replayed onto the
new tag and `main` is force-pushed. Nothing is ever lost, because every
published state is captured by a fork release tag. This keeps the delta small,
reviewable, and re-applicable indefinitely — currently 97 files against
`0.23.16`, of which `pixi.lock` is the single largest file.

## Remotes

`origin` is the fork; `upstream` is read-only. Push to `upstream` is disabled by
pointing its push URL at a bogus value, so a stray `git push upstream` fails
loudly instead of attempting to write to marimo-team.

```bash
git remote -v
# origin    https://github.com/luciorq/marimo-r.git (fetch)
# origin    https://github.com/luciorq/marimo-r.git (push)
# upstream  https://github.com/marimo-team/marimo (fetch)
# upstream  DISABLED_use_origin (push)
```

To recreate that setup on a fresh clone:

```bash
git clone https://github.com/luciorq/marimo-r.git
cd marimo-r
git remote add upstream https://github.com/marimo-team/marimo.git
git remote set-url --push upstream DISABLED_use_origin
git fetch upstream --tags
```

## Branches and tags

| Ref | Meaning |
| --- | --- |
| `origin/main` | The R-enabled tree: an upstream release tag plus the R patch series. Force-pushed on every sync. |
| `v<version>` | A fork release, e.g. `v0.24.0`. Immutable; this is what makes force-pushing `main` safe. Upstream's own tags have no `v` prefix, so they do not collide. |
| `backup/main-<timestamp>` | Created automatically by the sync script before each rebase. Local only; delete once the sync is verified. |
| `upstream/main`, upstream tags | Read-only, fetched from marimo-team. |

There is deliberately **no** long-lived `r-support` branch in the fork. The R
work *is* `main`; a separate branch would just be a second thing to keep rebased.

## Syncing with upstream

Sync on upstream **releases**, not on every `main` commit. Releases are tested
and give the fork a clean version to map onto.

```bash
scripts/fork/sync-upstream.sh check           # are we behind?
scripts/fork/sync-upstream.sh series          # what do we carry?
scripts/fork/sync-upstream.sh rebase 0.23.17  # replay the series onto a tag
```

`rebase` refuses to run on a dirty tree, creates a `backup/` branch, and replays
only the fork's own commits with `git rebase --onto`. On conflict it stops and
points at the hotspot list in `docs/development/r_support.md`; there is no magic
here, resolution is manual.

After the rebase, the verification gate is:

```bash
make py-check
make fe-check
uv run --group test pytest tests/_r tests/_server/test_lsp.py
cd frontend && pnpm test src/core/codemirror/language/__tests__/r.test.ts
```

Then `git push --force-with-lease origin main`.

A scheduled workflow,
[`.github/workflows/fork-upstream-check.yml`](.github/workflows/fork-upstream-check.yml),
runs `sync-upstream.sh check` every Monday and opens an `upstream-sync` issue
when a new upstream release appears. It only notifies — it never rebases.

### Keep the series squashed

Each rebase tends to produce a `fix(r): repair R support after rebase` commit.
Left alone, these accumulate one per sync forever, and every one of them gets
replayed (and can re-conflict) on every future rebase. **Fold them back into the
commit they repair** during the sync, so the series stays roughly:

1. `feat: R language support for marimo notebooks`
2. `test(r): add tests for R cells`
3. `docs(r): maintainer guide` / fork infrastructure

The series currently carries repair commits and two superseded design/plan docs
from the last rebase; squashing them is worth doing on the next sync.

## GitHub Actions

The fork carries upstream's entire `.github/workflows/` directory unchanged —
editing or deleting those files would create a conflict on every single rebase.
Instead, workflows that must not run here are **disabled at the repository
level**:

```bash
scripts/fork/disable-upstream-workflows.sh --repo luciorq/marimo-r
```

That covers publishing (`release*.yml`, `publish-{npm,docker}.yml`,
`discord-release.yml`), upstream-owned doc sites (`docs.yml`, `pages.yml`),
community bots (`cla.yml`, `marimo-bot.yml`, `labeler.yml`, `stale.yml`, …),
cron jobs that would just make noise (`link-check.yml`, `sync-llm-info.yml`), and
**all of upstream's test workflows**.

> This disabled state lives on GitHub, not in git. Re-run the script after
> creating the repository, after any repo transfer, and if GitHub ever
> re-enables a workflow. It is idempotent.

### Why upstream's tests are off

They were disabled when this repo was private and every minute billed (macOS
at 10x, Windows at 2x — upstream's suite was ~550 billed minutes per push).
The repo is public now and standard-runner minutes are free, but the suite
stays off for a better reason: none of it tests anything about R, and a wall
of always-green upstream jobs buries the signal from the handful that guard
the fork. For reference, what it would run per push:

| Workflow | Shape | Approx. billed minutes |
| --- | --- | --- |
| `test_be.yaml` | 11 Linux jobs + 2 macOS + 2 Windows + coverage | ~550 |
| `test_cli.yaml` | matrix over ubuntu / macos-14 / windows | ~150 |
| `dev_build.yaml` | ubuntu + macOS, fires on any `pyproject.toml` change | up to ~200 |
| `playwright.yml` | full e2e | ~30 |

That is well past the 2,000 free minutes/month in a handful of pushes — and
`dev_build.yaml` would fire on every release version bump. None of it tests
anything about R that `fork-ci.yml` does not.

### The two workflows this fork owns

- [`fork-ci.yml`](.github/workflows/fork-ci.yml) — the entire test story, Linux
  only, scoped to R support and the code it touches.
- [`fork-upstream-check.yml`](.github/workflows/fork-upstream-check.yml) — the
  weekly upstream release watcher described above.

`fork-ci.yml` is gated on the **`FORK_CI` repository variable**:

| `FORK_CI` | What runs | Measured cost |
| --- | --- | --- |
| `off` | nothing | 0 |
| `smoke` — the fallback when the variable is unset | Python R tests + LSP/formatter/interrupt tests + import smoke; frontend typecheck + R frontend tests | **~3 billed min** (2 Linux jobs) |
| `full` | the above plus the pixi R environment and the R integration tests | **~5 billed min** (3 Linux jobs) |
| `package` | builds the conda package and uploads it as a workflow artifact — never publishes | dispatch-only; builds the frontend, so slower |
| `publish` | the above, then uploads to prefix.dev/universe; needs `PREFIX_API_KEY` | dispatch-only |

Billing rounds each *job* up to the whole minute, so the figures above are
higher than the wall-clock total: a warm `full` run is about 3.1 minutes of
actual runtime across three jobs. Against upstream's ~550 billed minutes per
push, it still costs roughly 1%.

**All three jobs get their toolchain from `prefix-dev/setup-pixi`**, so CI runs
exactly what `pixi.lock` pins and there is no separate CI-only toolchain to
drift. That matters more than it sounds: while the frontend job was still on
upstream's `setup-node` action it ran **node 24**, whereas `pixi.lock` resolves
**node 26.6.0** — CI was typechecking the frontend against a different major
version than any developer runs. Moving it to pixi costs about 13 seconds per
run and removes that entire class of "works locally, fails in CI".

Each job installs only the environment it needs — `node` for the frontend,
`default` for Python, `default r` for R integration — so no job pays for a
toolchain it does not use.

The repository variable is set to **`full`**: standard-runner minutes are free
on a public repo, so every push runs the R integration suite.

```bash
gh variable set FORK_CI --body off   --repo luciorq/marimo-r  # silence CI
gh variable set FORK_CI --body smoke --repo luciorq/marimo-r  # cheapest useful level
```

A `workflow_dispatch` run takes a `level` input that overrides the variable for
that run, so you can get a `full` run without changing the repo default:

```bash
gh workflow run fork-ci.yml -f level=full --repo luciorq/marimo-r
```

Two things worth knowing about what `smoke` covers:

- **The frontend typecheck is the highest-value check in the whole workflow.**
  `user-config-form.tsx` and `notebook-lsp.ts` drift into type errors after a
  rebase without ever producing a git conflict — see the hotspot list in
  `docs/development/r_support.md`. A type error there is the only signal.
- **`smoke` runs without R installed**, so it sets `MARIMO_R_SKIP_TESTS=1`. The
  R integration tests *error* rather than skip when R is missing, so that
  variable is required, not an optimization. It also means `smoke` proves the
  Python and TypeScript glue survived a rebase — not that R still executes. Run
  `full` before tagging a release.

The earlier R↔DuckDB coverage gap is closed: `r-duckdb`, `r-dbi`, and
`r-ggplot2` are now in the pixi `r` feature, so those tests run rather than
skip. `r-duckdb` has no linux-aarch64 build on conda-forge and is scoped to
`linux-64`/`osx-arm64`; `r_backend.R` already guards its use behind
`requireNamespace()`, so aarch64 degrades gracefully.

## Packaging and releases

**Publishing is currently off.** Nothing is pushed to a channel, PyPI, npm, or
Docker Hub yet. The package builds and is tested; publishing it is a deliberate
manual step.

### The conda package is the supported artifact

Not a preference — a constraint. R is not pip-installable, and `r_backend.R`
does `library(jsonlite)` and `library(arrow)` at startup, while formatting
shells out to `air`. Those are conda packages. A wheel would import cleanly and
then fail the moment anyone ran an R cell, which is the entire point of the
fork. Conda can also state that `marimo-r` and upstream `marimo` cannot share a
prefix — they both own the `marimo` import package — which PyPI has no way to
express.

```bash
pixi run package     # frontend -> wheel -> conda package, into build/conda/
```

`recipe/recipe.yaml` is the source of truth. `[tool.pixi.package]` points at it
through the `pixi-build-rattler-build` backend, so `pixi build` and the recipe
cannot drift apart.

Optional R dependencies are **v3 extras**, mirroring the split already in
`[tool.pixi.feature.r.dependencies]` — anything guarded by `requireNamespace()`
in `r_backend.R`, or needed only by the LSP, is optional:

| Extra | Pulls in |
| --- | --- |
| `sql` | `r-dbi`, `r-duckdb` |
| `plots` | `r-ggplot2` |
| `lsp` | `r-languageserver`, `r-lintr`, `r-styler` |
| `lint` | `jarl` |

```bash
pixi add marimo-r --extras sql,lsp
```

Three things about this path are worth knowing before changing it:

- **It rests on three preview features**: v3 repodata, rattler-build's `--v3`,
  and pixi's `pixi-build` preview. rattler-build's own docs say the format "can
  and will change in backwards-incompatible ways" before v3 is final, so pin
  `rattler-build` and expect churn. Consuming a v3 package also needs a v3-aware
  resolver — pixi ≥ 0.71, recent conda — which narrows who can install it.
- **`r-duckdb` has no linux-aarch64 build**, so the `sql` extra drops it there
  with a build-time `if/then` selector. That is why the package is not `noarch`.
  v3's solve-time `when` cannot express it: rattler-build 0.73.0 rejects both
  negation (`not __osx`) and CPU-arch matchspecs. Verified per platform — the
  extra resolves to `[r-dbi, r-duckdb]` on linux-64 and osx-arm64, and
  `[r-dbi]` on linux-aarch64.
- **The recipe packages a pre-built wheel**, because the wheel must carry the
  built frontend (`marimo/_static`, `marimo/_lsp`), which needs the node
  toolchain. The build script asserts those are present rather than shipping a
  silently broken package. Note `dist/` is gitignored and a path source honours
  gitignore, so `use_gitignore: false` is load-bearing.

The package's own tests cover the thing unit tests cannot: that in a real
installed prefix, `resolve_r_invocation()` resolves R from that prefix with
library isolation, and that `jsonlite`, `arrow`, and `air` are actually there.

### The distribution name

The distribution is `marimo-r`; the **import** name stays `marimo`, so this is a
drop-in replacement that deliberately cannot be co-installed with upstream.

That rename is not a one-line change, because three places resolved the
distribution name at runtime and would have broken quietly:

- `marimo/_version.py` looked up `version("marimo")`. Unrenamed, `__version__`
  silently becomes `"unknown"`.
- **`marimo/_cli/sandbox.py` pinned `marimo=={version}` into generated
  sandboxes.** Left alone, every sandboxed notebook would have installed
  *upstream* marimo from PyPI — losing R support, and conflicting with this
  package since both own the `marimo` import name.
- `pyproject.toml`'s `recommended` extra referred to `marimo[sql]` and
  `marimo[sandbox]`, which would likewise have pulled upstream.

`marimo._version.DISTRIBUTION_NAME` is now the single source of truth; use it
rather than the literal string. A notebook written against upstream carries
`marimo==0.14.0` in its script metadata, so the sandbox *retargets* it to
`marimo-r` at the running version — upstream version specifiers are dropped
rather than translated, since they name releases that do not exist here. A pin
on `marimo-r` is respected as written.

### Publishing to prefix.dev

The package goes to the **`universe`** channel at prefix.dev, which is
**public**. Not conda-forge: this forks a package already in that channel, and
staged-recipes review would gate every release.

```bash
pixi run package                       # build first
pixi auth login prefix.dev --token …   # or set PREFIX_API_KEY
pixi run publish                       # upload
```

`publish` is deliberately *not* chained to `package`: uploading a given
version + build number is irreversible, so it is always an explicit act. In CI
it needs `FORK_CI=publish` — dispatch-only, never on push, never at the
`package` level — plus the `PREFIX_API_KEY` secret:

```bash
gh secret set PREFIX_API_KEY --repo luciorq/marimo-r
gh workflow run fork-ci.yml -f level=publish --repo luciorq/marimo-r
```

The job fails loudly if the level is `publish` and the secret is absent, rather
than building and quietly skipping the upload. That path is proven, not just
wired: build `hb0f4dca_1` was published by CI, with every step green including
`Publish to prefix.dev/universe`.

### Installing it

The channel is public — no authentication needed:

```toml
[workspace]
channels = ["https://repo.prefix.dev/universe", "conda-forge"]

[dependencies]
marimo-r = {version = "*", extras = ["lsp", "sql"]}
```

Bump the build number in `recipe/recipe.yaml` when republishing the same
version — the channel will reject a duplicate. Adding a *new platform* for an
already-published version is fine, though: platforms are separate subdirs.

#### Published so far

`marimo-r 0.24.0` (build `_0`, linux-64) is live, published by CI at
`level=publish` with the R integration suite green against the released
commit. `0.23.16.1` builds `_0`/`_1` remain from before the version-scheme
change. Verified by installing it from the
channel into a clean workspace: the `sql` extra resolved `r-dbi` and
`r-duckdb`, R resolved from the install prefix with `isolated: True`, and
`marimo.r("sum(1:10)")` returned `55`.

**linux-aarch64 is not published** — it needs a native arm runner.
osx-arm64 builds on `macos-latest` in the package/publish matrix; macOS
runners are free now that the repo is public, which is what unblocked it
(cross-building from Linux fails at `/usr/bin/codesign`).

### Still to do

- A PyPI wheel is still worth publishing as a secondary artifact for people who
  already have R and air on `PATH` — discovery falls back to `PATH` and works —
  but it cannot guarantee a working install and should not be the headline.

Until then, install from git:

```bash
# No release tag exists yet — tagging is blocked on a GPG passphrase.
uv pip install "git+https://github.com/luciorq/marimo-r@main"
```

When the fork is ready to publish, the plan is a distribution named **`marimo-r`**
that keeps the `marimo` import name. Flipping that switch is small — the
distribution name appears once, at `pyproject.toml:6` (`name = "marimo"`; the
`module-name = "marimo"` at line 316 is the hatch build target and must *not*
change). The rename is deliberately deferred until the first publish, so the
fork does not carry a needless diff against upstream in the meantime.

Two things to handle at that point:

- **`marimo-r` cannot be co-installed with `marimo`.** Both own the `marimo`
  import package, so installing one over the other silently shadows it. This
  must be stated prominently in the README.
- **Update the pixi path dependency**, `marimo = {path = ".", editable = true}`
  under `[tool.pixi.pypi-dependencies]`, to match the new name.

### Version scheme

**The version is upstream's, exactly.** `marimo-r 0.24.0` is marimo 0.24.0 plus
R support — same three-component format upstream uses.

An earlier scheme appended a fork serial (`0.24.0.1`). It was dropped for two
reasons. It invented a four-component version that marimo never publishes, so
`0.24.0.1` read like a marimo release that does not exist rather than "0.24.0
plus our build". And because `version` in `pyproject.toml` then differed from
upstream's, **that line conflicted on every single sync** — it was the only
manual conflict in the 0.24.0 rebase. Matching upstream removes the conflict
permanently.

Fork iterations of the same upstream release are **conda build numbers**, which
is exactly what they are for:

| Situation | What changes |
| --- | --- |
| Following a new upstream release | version tracks it: `0.24.0` → `0.24.1` |
| Rebuilding the same release (recipe fix, new dependency bound) | `build.number` in `recipe/recipe.yaml`: `_0` → `_1` |
| A fork-only *source* change with no upstream release | a PEP 440 post-release, `0.24.0.post1` |

Since the number alone no longer identifies which of the two you are running,
`marimo --version` reports the distribution as well:

```console
$ marimo --version
0.24.0 (marimo-r)
```

Three places carry the version — `[project]`, `[tool.pixi.package]`, and the
recipe's `context`. The first now comes through the rebase for free; the other
two are updated by hand, and the CI packaging job asserts all three agree.

Tag a fork release `v<version>` (e.g. `v0.24.0`); upstream's own tag has no `v`
prefix, so they do not collide. Rebuilds need no tag — the source is unchanged.

## Current state

As of the last sync, `main` is based on `0.23.16` plus 17 unreleased upstream
commits — a leftover from rebasing onto `upstream/main` rather than onto a tag.
The next sync should rebase onto the `0.23.17` tag exactly, which normalizes the
base and makes the version scheme above line up.
