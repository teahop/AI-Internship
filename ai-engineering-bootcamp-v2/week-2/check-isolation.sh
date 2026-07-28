#!/usr/bin/env bash
# TEMPORARY — delete after Week 2 / Session 2 homework is submitted.
# Fails (exit 1) if the working tree looks like capstone homework bleed.
set -euo pipefail

# Script lives at AI-Internship/ai-engineering-bootcamp-v2/week-2/
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ ! -d "$ROOT/.git" ]]; then
  echo "ERROR: expected Git root at $ROOT (AI-Internship/). Fix path or run from the repo."
  exit 1
fi

RED=$'\033[31m'
GRN=$'\033[32m'
YLW=$'\033[33m'
RST=$'\033[0m'

fail=0

echo "=== Week 2 homework isolation check ==="
echo "Repo: $ROOT"
echo "Branch: $(git branch --show-current 2>/dev/null || echo '?')"
echo

# Capstone paths that must not change during homework work.
# Ignore local caches/logs that often sit untracked in week-1.
week1_hits="$(
  git status --porcelain \
    | grep -E 'ai-engineering-bootcamp-v2/week-1/' \
    | grep -Ev '_ab_ledger_cache\.json|__pycache__|\.venv/|\.pyc$|\.log$|measure_stage.*\.json$' \
    || true
)"
if [[ -n "$week1_hits" ]]; then
  echo "${RED}FAIL${RST}: working tree has changes under week-1/ (capstone)."
  echo "       Stash, commit on main separately, or discard — do not mix with RAG homework."
  echo "$week1_hits"
  fail=1
else
  echo "${GRN}OK${RST}: no week-1/ source changes in working tree."
  ignored="$(
    git status --porcelain \
      | grep -E 'ai-engineering-bootcamp-v2/week-1/' \
      || true
  )"
  if [[ -n "$ignored" ]]; then
    echo "${YLW}NOTE${RST}: ignored local week-1 cache/noise (not blocking):"
    echo "$ignored"
  fi
fi

# Show what is changing under week-2
week2_changes="$(git status --porcelain | grep -E 'ai-engineering-bootcamp-v2/week-2/' || true)"
if [[ -n "$week2_changes" ]]; then
  echo "${GRN}OK${RST}: week-2/ changes present (expected for homework):"
  echo "$week2_changes"
else
  echo "${YLW}NOTE${RST}: no week-2/ changes yet — fine before you start coding."
fi

echo
echo "Allowed homework root:"
echo "  ai-engineering-bootcamp-v2/week-2/rag-homework/"
echo "Guide: ai-engineering-bootcamp-v2/week-2/HOMEWORK_ISOLATION.md"
echo

if [[ "$fail" -ne 0 ]]; then
  echo "${RED}Isolation check failed.${RST}"
  exit 1
fi

echo "${GRN}Isolation check passed.${RST}"
exit 0
