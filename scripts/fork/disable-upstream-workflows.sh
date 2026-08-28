#!/usr/bin/env bash
# Disable the upstream workflows that must never run in this fork.
#
# This fork carries marimo's full .github/workflows/ directory so that rebasing
# onto upstream stays conflict-free. Rather than deleting or editing those files
# (which would conflict on every sync), we disable them at the repository level
# with `gh workflow disable`. That state lives on GitHub, not in git — so re-run
# this script after creating the repo, and any time GitHub re-enables something.
#
# Usage: scripts/fork/disable-upstream-workflows.sh [--repo luciorq/marimo-r]

set -euo pipefail

REPO="luciorq/marimo-r"
if [[ "${1:-}" == "--repo" ]]; then
  REPO="${2:?--repo requires a value}"
fi

# Publishing: would attempt to release to marimo's PyPI / npm / Docker Hub.
# Community automation: acts on issues and PRs that only make sense upstream.
# Scheduled jobs: fire on cron in a fork and produce nothing but noise.
DISABLE=(
  # publishing
  release.yml
  release-prod.yml
  release-dev.yml
  release-tag.yml
  release-marimo-base.yml
  publish-docker.yml
  publish-npm.yml
  discord-release.yml
  # docs sites owned by upstream
  docs.yml
  pages.yml
  # community / bot automation
  cla.yml
  marimo-bot.yml
  labeler.yml
  enforce-label.yml
  notify-readme-owners.yml
  stale.yml
  # scheduled noise
  link-check.yml
  sync-llm-info.yml
  # Upstream's test suite. Sized for upstream's budget, not a private fork's:
  # test_be alone fans out to ~15 jobs including macOS (billed at 10x) and
  # Windows (2x), which is several hundred billed minutes per push to main.
  # fork-ci.yml replaces all of it with Linux-only jobs scoped to R support.
  test_be.yaml
  test_fe.yaml
  test_cli.yaml
  test_no_build.yaml
  test_schemas.yaml
  test_typos.yaml
  test_design_md.yaml
  test_marimo_lsp.yaml
  playwright.yml
  dev_build.yaml
)

echo "Disabling upstream workflows in $REPO"
for wf in "${DISABLE[@]}"; do
  if gh workflow disable "$wf" --repo "$REPO" >/dev/null 2>&1; then
    echo "  disabled  $wf"
  else
    # workflow_call-only workflows have no trigger to disable, and a workflow
    # GitHub has not registered yet cannot be addressed by name.
    echo "  skipped   $wf (not registered or not directly triggerable)"
  fi
done

echo
echo "Still enabled (intentionally):"
gh workflow list --repo "$REPO" --limit 100 |
  awk -F'\t' '$2 == "active" { print "  " $1 }'
