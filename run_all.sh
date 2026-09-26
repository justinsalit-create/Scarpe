#!/usr/bin/env bash
# Full run: one borough at a time, committing + pushing after each so progress survives restarts.
set -u
cd "$(dirname "$0")"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
push() { for d in 2 4 8 16; do git push -q -u origin "$BRANCH" && return; sleep $d; done; }
python3 -c "from londonfood.boroughs import BOROUGHS; print('\n'.join(BOROUGHS))" | while read -r b; do
  short=$(python3 -c "import sys; from londonfood.boroughs import short_name; print(short_name(sys.argv[1]).replace(' ', '_'))" "$b")
  [ -f "output/runs/$short/london_food_emails.csv" ] && continue
  echo "=== $b ==="
  python3 -m londonfood --boroughs "$b" --out "output/runs/$short" --workers 24 || { echo "FAILED: $b"; continue; }
  git add output/runs; git add -f .cache 2>/dev/null
  git commit -qm "Data: $b" && push
done
python3 -m londonfood --out output --workers 24
git rm -rq --cached output/runs 2>/dev/null; rm -rf output/runs
git add output; git add -f .cache 2>/dev/null
git commit -qm "Data: combined London food venue contacts (all boroughs)" && push
echo ALL DONE
