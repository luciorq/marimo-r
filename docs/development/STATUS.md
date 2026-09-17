# Fork status and session restart guide

Last updated: 2026-09-17. This is the handoff document: current state, open
items with exact commands, and the restart prompt at the bottom. The durable
references it leans on:

- [FORK.md](../../FORK.md) — the maintenance model: sync/rebase flow, version
  scheme, CI levels, packaging, publishing, install instructions.
- [r_support.md](r_support.md) — R architecture, file inventory, and the
  conflict-hotspot playbook for rebases.
- [marimo/_r/install.py](../../marimo/_r/install.py) — the registration
  inventory: every point where R support attaches to upstream, and the direct
  edits that cannot become registrations.

## Current state

- **Base**: upstream `0.24.2`, fully rebased, series is 8 clean commits
  (`git log --oneline 0.24.2..HEAD`). All gates green: `pixi run make check`,
  285+ backend tests, frontend R tests, `r-doctor`, CI at `FORK_CI=full`.
- **Repo and channel are public.** CI default is `full` (free minutes);
  package/publish builds linux-64 + osx-arm64.
- **Released**: `0.24.2` (linux-64 + osx-arm64, tag `v0.24.2`), `0.24.0`
  (linux-64, tag `v0.24.0`) and `0.23.16.1` on
  `https://repo.prefix.dev/universe`. Tags are unsigned-annotated. The
  `PREFIX_API_KEY` secret and `~/.rattler/credentials.json` both hold the
  key rotated on 2026-09-17, so CI `publish` and local `pixi run publish`
  both work.

## Open items, in order

1. **Nothing release-blocking.** Next release: bump `pyproject.toml` and
   `recipe/recipe.yaml`, dispatch `fork-ci.yml -f level=publish`, tag with
   `-c tag.gpgsign=false`, update FORK.md's "Published so far". If the key is
   ever rotated again, `pixi auth login prefix.dev --token …` validates it
   before `gh secret set PREFIX_API_KEY`.
2. **conda-forge r-languageserver 0.3.19** — CRAN has it; conda-forge is on
   0.3.18. Pre-verified compatible over marimo's own wire (identical caps,
   completions, diagnostics), so take it when the feedstock bumps. No pin
   ceiling needed.
3. **Known deferred**: linux-aarch64 package (needs an arm runner);
   StylerFormatter is fixed but styler has no line-width control; upstream's
   3 `VercelAIAdapter` mypy errors (their files, ride until they fix);
   the optional PyPI wheel as a "bring your own R" secondary artifact;
   the upstream language-plugin-API proposal (install.py is the evidence).

## Findings that keep paying rent

- **PATH-only lookups are the fork's recurring bug class.** Anything
  resolving R-ecosystem tools must go through `marimo._r.launcher.find_r_tool`
  (and subprocesses need `build_environment`'s matched env). Found and fixed
  five separate times: session, LSP, capability, air gate, styler.
- **Upstream registries the fork must opt into produce no conflict markers.**
  0.24.0's capability tiers broke reset silently. The guard is upstream's
  completeness tests (in CI smoke); the hotspot list in r_support.md explains.
- **Verify empirically, not from docs**: air's "files only" comment was wrong
  (stdin works, `--stdin-file-path`); jarl publishes only on `didSave`;
  languageserver 0.3.19's "no breaking changes" checked over the real wire.
- **A 401 that survives re-setting the secret is the key, not the secret.**
  Verify a credential locally before wiring it into CI; prefix.dev keys can
  be scoped to channels, and revoked keys fail the same way as absent ones.
- **`assert` every scripted `str.replace`** during conflict resolution — one
  unasserted replace committed conflict markers mid-series in the 0.24.2 sync.
- **Sync playbook**: `scripts/fork/sync-upstream.sh check|series|rebase`;
  regenerate (don't merge) snapshots; fold repair commits into the 5-commit
  shape; verify the folded tree is byte-identical to the pre-fold tip; check
  every series commit for conflict markers before force-pushing.

## Restart prompt

> This is the marimo-r fork (github.com/luciorq/marimo-r): marimo plus R cell
> support, managed with pixi, packaged as a conda package on the public
> prefix.dev `universe` channel. Read docs/development/STATUS.md for current
> state and open items, FORK.md for the maintenance model, and
> docs/development/r_support.md before touching R code or rebasing.
> Start by running `scripts/fork/sync-upstream.sh check` and
> `pixi run r-doctor`, then pick up the open items in STATUS.md.
