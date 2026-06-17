#!/usr/bin/env bash
# Example bento land-work `pre` hook: run the strict storystore audit as a
# pre-merge landing gate.
#
# WHY: stories-audit is read-only and nothing schedules it, so story/software
# drift in a consuming repo is never caught unless something runs the audit.
# Wiring it into land-work's `pre` position turns every merge into a checkpoint:
# a clean corpus lands, a corpus with audit findings is blocked.
#
# INSTALL: copy this file into your project's bento land-work hook-scripts dir
# and make it executable. See bento's project-hook-scripts reference for the
# discovery roots; the repo-scoped location is:
#
#   <repo-root>/.agent-plugins/bento/bento/land-work/hook-scripts/pre/30-stories-audit.sh
#   chmod +x <repo-root>/.agent-plugins/bento/bento/land-work/hook-scripts/pre/30-stories-audit.sh
#
# BEHAVIOR:
#   - exits 0 immediately when docs/stories/INDEX.md is absent (no corpus to
#     audit, so the gate is a no-op);
#   - otherwise runs `shared/audit.py --repo-root <root> --strict` and exits
#     nonzero when the audit reports findings, halting the merge.
#
# LOCATING THE PLUGIN: the audit lives at <storystore-plugin-root>/shared/audit.py.
# This script resolves that directory, in order, via:
#   1. $STORYSTORE_SHARED, when set and containing audit.py. This is the
#      recommended way to pin a specific install (set it in your repo or CI
#      environment) and mirrors the $STORYSTORE_SHARED preamble used in the
#      storystore SKILL.md files.
#   2. this script's own location, when it still sits inside the storystore
#      plugin tree at examples/land-work/hook-scripts/pre/ (shared/ is four
#      levels up).
#   3. a best-effort scan of the Claude plugin cache.
# If none resolve, it exits nonzero and asks you to set $STORYSTORE_SHARED,
# rather than guessing a path.

set -euo pipefail

# land-work exports BENTO_HOOK_REPO_ROOT; fall back to the cwd so the script is
# also runnable by hand from a repo root.
repo_root="${BENTO_HOOK_REPO_ROOT:-$PWD}"

# No stories corpus → nothing to audit; pass the gate immediately.
if [ ! -f "$repo_root/docs/stories/INDEX.md" ]; then
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_shared() {
  # 1. Explicit override (recommended for pinned/CI installs).
  if [ -n "${STORYSTORE_SHARED:-}" ] && [ -f "${STORYSTORE_SHARED}/audit.py" ]; then
    (cd "$STORYSTORE_SHARED" && pwd)
    return 0
  fi
  # 2. Script still inside the plugin tree: shared/ is four levels up from
  #    examples/land-work/hook-scripts/pre/.
  if [ -f "$script_dir/../../../../shared/audit.py" ]; then
    (cd "$script_dir/../../../../shared" && pwd)
    return 0
  fi
  # 3. Best-effort scan of the Claude plugin cache. Glob order is lexical,
  #    not newest-first, so with multiple cached versions this is
  #    nondeterministic — set $STORYSTORE_SHARED to pin a specific install.
  local cache
  for cache in \
    "$HOME"/.claude/plugins/cache/*/storystore/*/shared \
    "$HOME"/.claude/plugins/cache/storystore/*/shared; do
    if [ -f "$cache/audit.py" ]; then
      (cd "$cache" && pwd)
      return 0
    fi
  done
  return 1
}

if ! STORYSTORE_SHARED="$(resolve_shared)"; then
  cat >&2 <<'MSG'
30-stories-audit: cannot locate the storystore plugin's shared/audit.py.
Set STORYSTORE_SHARED to the directory containing audit.py, e.g.:
  export STORYSTORE_SHARED="$HOME/.claude/plugins/cache/<marketplace>/storystore/<version>/shared"
MSG
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "30-stories-audit: python3 not found on PATH; cannot run the audit." >&2
  exit 1
fi

# audit.py exits 1 under --strict when findings exist; exec propagates that
# exit code to land-work, which blocks the merge on any nonzero hook exit.
exec python3 "$STORYSTORE_SHARED/audit.py" --repo-root "$repo_root" --strict
