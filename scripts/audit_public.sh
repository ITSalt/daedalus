#!/usr/bin/env bash
# Scan the working tree and the full git history for things that must not be public:
# secrets, internal addresses, tooling trailers, machine paths, and build artefacts.
# Run before opening the repository, and before any push.
#
# Every check gates. An exit of 0 is the claim that this tree may be published, so a check that only
# prints is worse than no check: it is the one that was passing when `desktop/desktop` went in.
#
# Four checks, in the order they are printed:
#
#   patterns   text that is a secret or an internal address, in the tree, in every blob in history,
#              and in every commit message. The project's own `Co-authored-by: Daedalus` trailer is
#              what the agent signs its commits with and is meant to be there; every other
#              co-author line, and every session link, is not.
#   binaries   a tracked file git treats as binary that is not one of the assets this repository is
#              supposed to carry, plus anything at all whose first bytes say ELF, Mach-O or PE. A
#              compiled binary is not just weight: an unstripped one carries the absolute path of
#              every source file it was built from, which is the machine it was built on.
#   paths      /home/<name> and /Users/<name> outside the placeholder names the docs and tests use
#              deliberately, and — separately, and without ever writing it down here — the name of
#              the account this is being run from.
#
# `--self-check` runs the whole thing against a fixture repository carrying one of each fault, and
# fails unless every rule fires. That is the part that keeps this honest: a scanner nobody has seen
# fail is a scanner nobody knows works.
set -euo pipefail

PATTERNS='sk-[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]+|ghp_[A-Za-z0-9]{20,}|[0-9]{6,}:[A-Za-z0-9_-]{30,}|192\.168\.[0-9.]+|10\.10\.[0-9.]+|Co-authored-by|Generated with|[Cc]laude-[Ss]ession|session_01|claude\.ai/code|Signed-off-by'

# Where this repository legitimately keeps bytes git cannot diff. Anchored at the start of the path,
# so a binary that merely ends in one of these names does not slip through on the suffix. The
# screenshots may sit one level down, in a folder named by a language code: the same set in Russian.
# The terminal's typeface is bundled with the app (its licence sits beside it) and is the one font.
BINARY_ALLOWED='^docs/(brand|diagrams|screenshots(/[a-z]{2})?)/[^/]+\.(png|jpg|jpeg|webp|gif|svg)$|^miniapp/public/.+\.(png|jpg|ico|webp|svg)$|^miniapp/src/terminal/fonts/[^/]+\.woff2$|^skills/.+\.(png|jpg|jpeg|webp|gif|ttf|otf|woff2?|pdf|tar\.gz|zip)$'

# Usernames that appear in documentation and tests on purpose, as examples. Everything else is a real
# account name and has no business being committed.
PLACEHOLDER_USERS='someone|operator|keyproxy|user|admin|ada|you|a|o|x|y|z'

# Files whose whole point is to carry one of the patterns above: the scanner itself, the agent's own
# commit-trailer documentation, and the fixtures the redaction tests are built out of.
PATTERN_EXEMPT='(scripts/audit_public\.sh|README\.md|daedalus/extensions/selfdev\.py|skills/self-develop/SKILL\.md|tests/)'

OWN_TRAILER='Co-authored-by: daedalus'

# Anything whose first bytes are one of these is an executable image, wherever it sits and whatever it
# is called. `desktop/desktop` had no extension to catch it by.
is_executable_image() {
  local head
  head=$(head -c 4 -- "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')
  case "$head" in
    7f454c46*) return 0 ;;   # ELF
    cffaedfe*|cefaedfe*|cafebabe*) return 0 ;;   # Mach-O, and a fat binary
    4d5a*) return 0 ;;       # PE / MZ
    *) return 1 ;;
  esac
}

audit() {
  local failed=0

  echo "== working tree"
  if git grep -InE "$PATTERNS" -- . 2>/dev/null | grep -EvI "$PATTERN_EXEMPT" | grep -vi "$OWN_TRAILER"; then failed=1; else echo "clean"; fi

  echo "== history (all blobs)"
  local history_hits
  # Captured, then tested -- never tested by the pipeline's status. Under pipefail the status of
  # `cmd | while ...; done | grep ...` is the status of the loop's LAST iteration, so a pattern
  # present in an older commit and absent from the newest one printed its line here and answered
  # "clean": the verdict hung on the order `git rev-list --all` happens to visit commits in.
  history_hits=$(git rev-list --all | while read -r c; do git grep -InE "$PATTERNS" "$c" -- . 2>/dev/null | sed "s/^/$c:/"; done |
    grep -EvI "$PATTERN_EXEMPT" | grep -vi "$OWN_TRAILER" || true)
  if [ -n "$history_hits" ]; then printf '%s\n' "$history_hits"; failed=1; else echo "clean"; fi

  echo "== commit messages"
  if git log --all --format='%H %s%n%b' | grep -inE "$PATTERNS" | grep -vi "$OWN_TRAILER"; then failed=1; else echo "clean"; fi

  echo "== tracked binaries"
  local found=""
  # The empty tree: diffing HEAD against it lists every tracked file, and numstat prints "-\t-" for
  # each one git cannot produce a line count for — which is exactly its own definition of binary.
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if ! printf '%s' "$path" | grep -qE "$BINARY_ALLOWED"; then
      found="$found$path (binary, not an allowed asset)"$'\n'
    fi
  done < <(git diff --numstat 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD 2>/dev/null | awk -F'\t' '$1=="-" && $2=="-" {print $3}')
  while IFS= read -r path; do
    [ -f "$path" ] || continue
    if is_executable_image "$path"; then
      found="$found$path (compiled executable — it carries the paths it was built from)"$'\n'
    fi
  done < <(git ls-files)
  if [ -n "$found" ]; then printf '%s' "$found"; failed=1; else echo "clean"; fi

  echo "== machine paths"
  local hits
  # The name must begin with a letter: a URL path like news/home/20220228005327 is not an account.
  hits=$(git grep -InE '/(home|Users)/[a-z][A-Za-z0-9_.-]*' -- . 2>/dev/null |
    grep -vE 'scripts/audit_public\.sh' |
    grep -EvI "/(home|Users)/($PLACEHOLDER_USERS)([^A-Za-z0-9_.-]|$)" || true)
  # The one name that must never appear is read from the environment rather than written down: a
  # scanner that spells out the account it is guarding against publishes it in the file that guards it.
  local me home
  me=$(id -un 2>/dev/null || echo "")
  home=$(basename -- "${HOME:-/}")
  for name in "$me" "$home"; do
    [ -n "$name" ] && [ "$name" != "/" ] || continue
    hits="$hits$(git grep -InF "/home/$name" -- . 2>/dev/null | grep -vE 'scripts/audit_public.sh' || true)"
    hits="$hits$(git grep -InF "/Users/$name" -- . 2>/dev/null | grep -vE 'scripts/audit_public.sh' || true)"
  done
  hits=$(printf '%s\n' "$hits" | grep -v '^$' || true)
  if [ -n "$hits" ]; then printf '%s\n' "$hits"; failed=1; else echo "clean"; fi

  return "$failed"
}

# -- the self-check ---------------------------------------------------------------------------
#
# Builds a throwaway repository holding one of each fault the audit above looks for -- a committed
# ELF binary, a file naming the account this is running as, a provider token, an internal address,
# and a commit message carrying tooling trailers -- and requires the audit to refuse every one of
# them by name. Then requires the real tree to pass.
#
# One fault per rule, and the rules are the whole of `PATTERNS`: a fixture carrying only the faults
# the scanner was written against first proves nothing about the rest of the list. Measured before
# this fixture was widened: deleting all four provider-token patterns from `PATTERNS` left this
# self-check green, so a repository carrying a token was scanned clean by an audit that still said
# it had checked itself.

self_check() {
  local fixture status
  fixture=$(mktemp -d)
  (
    cd "$fixture"
    git init -q .
    git config user.email a@b.c
    git config user.name a
    printf '\177ELF\002\001\001\000 built somewhere\n' > tool
    mkdir -p notes
    printf 'the build ran in /home/%s/checkout\n' "$(id -un)" > notes/build.md
    # One credential-shaped line per kind `PATTERNS` knows, and one file naming an address on each
    # of the two private blocks it looks for. Every value here is ASSEMBLED at run time rather than
    # written down, for the same reason the account name above is read from the environment: the
    # file that guards against a value has no business carrying it. None of these is real; each is
    # a repeated byte behind the prefix that gives it its shape.
    filler=$(printf 'A%.0s' $(seq 1 40))
    {
      printf '%s_%s\n' ghp "$filler"
      printf '%s-%s\n' sk "$filler"
      printf '%s_%s\n' github_pat "$filler"
      printf '%s:%s\n' 1234567 "$filler"
    } > config.env
    printf 'the console answers at http://%s.%s.9.9:8080 and the proxy at http://%s.%s.0.9:9000\n' \
      192 168 10 10 > notes/network.md
    git add -A
    git commit -qm "a fixture

Co-authored-by: someone <someone@example.invalid>
Generated with a tool: see the session log at https://example.invalid/session_01"
  )
  status=0
  bash "$SELF" "$fixture" > "$fixture/out.txt" 2>&1 || status=$?
  local report
  report=$(cat "$fixture/out.txt")
  rm -rf "$fixture"
  if [ "$status" -eq 0 ]; then
    echo "SELF-CHECK FAILED: the audit passed a repository with a committed binary and a machine path"
    printf '%s\n' "$report"
    return 1
  fi
  printf '%s' "$report" | grep -q "tool (compiled executable" || {
    echo "SELF-CHECK FAILED: the committed binary was not named"; printf '%s\n' "$report"; return 1; }
  printf '%s' "$report" | grep -q "notes/build.md" || {
    echo "SELF-CHECK FAILED: the machine path was not named"; printf '%s\n' "$report"; return 1; }
  printf '%s' "$report" | grep -q "config.env" || {
    echo "SELF-CHECK FAILED: the provider token was not named"; printf '%s\n' "$report"; return 1; }
  printf '%s' "$report" | grep -q "notes/network.md" || {
    echo "SELF-CHECK FAILED: the internal address was not named"; printf '%s\n' "$report"; return 1; }
  printf '%s' "$report" | grep -q "someone@example.invalid" || {
    echo "SELF-CHECK FAILED: the tooling trailer was not named"; printf '%s\n' "$report"; return 1; }
  echo "self-check: the audit refuses a committed binary and a machine path, a provider token, an internal address and a tooling trailer"

  local out
  out=$(bash "$SELF" "$ROOT" 2>&1) || {
    echo "SELF-CHECK FAILED: the audit does not pass this tree"
    printf '%s\n' "$out"
    return 1
  }
  echo "self-check: the audit passes this tree"

  # A place, not a family: the same rule, applied where the fixture never puts it. Every fault in the
  # fixture above is committed, so it lives in the working tree AND in history at once and nothing in
  # it separates "the history reader works" from "it is dead and another check caught the bytes".
  # Here the pattern exists ONLY in an older commit: it is in no file of the tree and in no message.
  local older status2 report2
  older=$(mktemp -d)
  (
    cd "$older"
    git init -q .
    git config user.email a@b.c
    git config user.name a
    git commit -q --allow-empty -m "an empty tree"
    printf 'a key %s_%s\n' ghp "$(printf 'A%.0s' $(seq 1 40))" > gone.txt
    git add -A
    git commit -qm "a file that will not stay"
    git rm -q gone.txt
    git commit -qm "and is gone from the tree"
  )
  status2=0
  bash "$SELF" "$older" > "$older/out.txt" 2>&1 || status2=$?
  report2=$(cat "$older/out.txt")
  rm -rf "$older"
  if [ "$status2" -eq 0 ]; then
    echo "SELF-CHECK FAILED: the audit passed a repository whose only credential is in an older commit"
    printf '%s\n' "$report2"
    return 1
  fi
  echo "self-check: the audit refuses a pattern that survives only in history"
}

SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
ROOT=$(cd "$(dirname "$0")/.." && pwd)

if [ "${1:-}" = "--self-check" ]; then
  self_check
else
  # A directory argument audits that repository instead of this one; the self-check uses it, and
  # nothing else should.
  cd "${1:-$ROOT}"
  audit
fi
