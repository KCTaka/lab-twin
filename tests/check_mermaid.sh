#!/usr/bin/env bash
# Render every ```mermaid block in the repo's Markdown with mermaid-cli; non-zero exit on any syntax error.
#   MMDC=/path/to/mmdc PPTR=/path/to/puppeteer.json tests/check_mermaid.sh [out_dir]
set -uo pipefail
cd "$(dirname "$0")/.."
MMDC="${MMDC:-npx -y @mermaid-js/mermaid-cli@11.4.2}"
OUT="${1:-build/mermaid}"
mkdir -p "$OUT"
fail=0; n=0
for md in README.md docs/*.md docs/stage_sheets/*.md; do
  awk -v base="$OUT/$(echo "$md" | tr '/' '_')" '
    /^```mermaid/ {i++; f=sprintf("%s_%02d.mmd", base, i); inblk=1; next}
    /^```/ && inblk {inblk=0; close(f); next}
    inblk {print > f}' "$md"
done
for f in "$OUT"/*.mmd; do
  [ -e "$f" ] || continue
  n=$((n+1))
  if $MMDC ${PPTR:+-p "$PPTR"} -q -i "$f" -o "${f%.mmd}.svg" >"${f%.mmd}.log" 2>&1; then
    echo "OK   $(basename "$f")"
  else
    echo "FAIL $(basename "$f")"; sed -n 1,8p "${f%.mmd}.log"; fail=1
  fi
done
echo "$n diagrams checked"
exit $fail
