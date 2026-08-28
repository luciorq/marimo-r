#!/usr/bin/env bash
# Sync this fork's R support patch series onto a new upstream marimo release.
#
# This fork keeps a small, linear series of R commits rebased on top of an
# upstream release tag. See FORK.md for the full model, and
# docs/development/r_support.md for how to resolve the conflicts this script
# will hand you.
#
# Usage:
#   scripts/fork/sync-upstream.sh check          # is a newer upstream release out?
#   scripts/fork/sync-upstream.sh series         # show the R commits we carry
#   scripts/fork/sync-upstream.sh rebase [TAG]   # rebase onto TAG (default: latest)

set -euo pipefail

UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"
TAG_GLOB='[0-9]*.[0-9]*.[0-9]*'

die() {
  echo "error: $*" >&2
  exit 1
}

fetch_upstream() {
  git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1 ||
    die "no '$UPSTREAM_REMOTE' remote; see FORK.md → Remotes"
  git fetch --quiet --tags --prune "$UPSTREAM_REMOTE"
}

# Newest upstream release tag, e.g. 0.23.16.
latest_tag() {
  git tag --list --sort=-v:refname "$TAG_GLOB" | head -n 1
}

# Newest upstream release tag that is already an ancestor of HEAD.
current_base_tag() {
  git describe --tags --abbrev=0 --match "$TAG_GLOB" \
    "$(git merge-base HEAD "$UPSTREAM_REMOTE/main")"
}

# The R commits this fork carries, i.e. everything on HEAD that is not upstream.
series_range() {
  echo "$(git merge-base HEAD "$UPSTREAM_REMOTE/main")..HEAD"
}

cmd_check() {
  fetch_upstream
  local latest base
  latest="$(latest_tag)"
  base="$(current_base_tag)"

  echo "fork branch:      $(git rev-parse --abbrev-ref HEAD)"
  echo "upstream base:    $base"
  echo "latest upstream:  $latest"

  if git merge-base --is-ancestor "$latest" HEAD; then
    local extra
    extra="$(git rev-list --count "$latest..$(git merge-base HEAD "$UPSTREAM_REMOTE/main")")"
    if [[ "$extra" -gt 0 ]]; then
      echo "status:           up to date (based on $base + $extra unreleased upstream commits)"
    else
      echo "status:           up to date"
    fi
    return 0
  fi

  echo "status:           BEHIND — $latest is available"
  echo
  echo "New upstream commits to absorb: $(git rev-list --count "HEAD..$latest")"
  echo "Run: scripts/fork/sync-upstream.sh rebase $latest"
  return 1
}

cmd_series() {
  fetch_upstream
  local range
  range="$(series_range)"
  echo "R patch series ($range):"
  git log --oneline --reverse "$range"
  echo
  git diff --stat "${range/../...}" | tail -n 1
}

cmd_rebase() {
  local target="${1:-}"
  fetch_upstream
  [[ -n "$target" ]] || target="$(latest_tag)"
  git rev-parse --verify --quiet "$target^{commit}" >/dev/null ||
    die "unknown upstream tag: $target"

  [[ -z "$(git status --porcelain)" ]] ||
    die "working tree is dirty; commit or stash first"

  local branch backup range
  branch="$(git rev-parse --abbrev-ref HEAD)"
  [[ "$branch" != "HEAD" ]] || die "detached HEAD; check out the fork branch first"

  range="$(series_range)"
  echo "Rebasing $branch onto upstream $target"
  echo "Patch series being replayed:"
  git log --oneline --reverse "$range" | sed 's/^/  /'
  echo

  backup="backup/${branch}-$(date +%Y%m%d-%H%M%S)"
  git branch "$backup"
  echo "Backup branch created: $backup"
  echo

  if git rebase --onto "$target" "$(git merge-base HEAD "$UPSTREAM_REMOTE/main")" "$branch"; then
    echo
    echo "Rebase clean. Now verify before pushing:"
  else
    echo
    echo "Rebase stopped on a conflict. Resolve it, then:"
    echo "  git add -A && GIT_EDITOR=true git rebase --continue"
    echo "See docs/development/r_support.md → 'Known conflict hotspots'."
    echo "To abort:  git rebase --abort   (or: git reset --hard $backup)"
    echo
    echo "Once the rebase finishes, verify before pushing:"
  fi

  cat <<EOF
  make py-check
  make fe-check
  uv run --group test pytest tests/_r tests/_server/test_lsp.py
  cd frontend && pnpm test src/core/codemirror/language/__tests__/r.test.ts

Then publish the new series (bump 'version' in pyproject.toml to $target.N first):
  git push --force-with-lease origin $branch
  git tag v$target.1 && git push origin v$target.1

Update docs/development/r_support.md with any new conflict hotspots you hit.
EOF
}

case "${1:-check}" in
  check) cmd_check ;;
  series) cmd_series ;;
  rebase) shift; cmd_rebase "$@" ;;
  *) die "unknown command: $1 (expected: check | series | rebase)" ;;
esac
