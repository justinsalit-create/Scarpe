#!/usr/bin/env bash
# Verified website discovery in batches (busiest-missing neighbourhoods first), committing after each batch.
set -u
cd "$(dirname "$0")"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
push() { for d in 2 4 8 16; do git push -q -u origin "$BRANCH" && return; sleep $d; done; }
while true; do
  python3 -m londonfood --out output --workers 24 --discover --discover-limit 5000 2>&1 | tee /tmp/claude-0/discover_batch.log
  git add output; git add -f .cache 2>/dev/null
  git commit -qm "Data: verified website discovery batch" && push
  grep -q " 0 to try this run" /tmp/claude-0/discover_batch.log && break
done
echo DISCOVERY DONE
