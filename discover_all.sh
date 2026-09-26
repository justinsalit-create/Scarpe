#!/usr/bin/env bash
# Verified website discovery in batches (busiest-missing neighbourhoods first), committing after each batch.
# The live cache sits outside the repo (it changes constantly); a snapshot is committed to cache/ per batch.
set -u
cd "$(dirname "$0")"
LIVE=${LONDONFOOD_CACHE:-$HOME/.londonfood-cache}
[ -d "$LIVE" ] || cp -a cache "$LIVE"    # fresh container: resume from the committed snapshot
BRANCH=$(git rev-parse --abbrev-ref HEAD)
push() { for d in 2 4 8 16; do git push -q -u origin "$BRANCH" && return; sleep $d; done; }
while true; do
  python3 -m londonfood --out output --cache "$LIVE" --workers 24 --discover --discover-limit 2000 2>&1 \
    | tee /tmp/claude-0/discover_batch.log
  rm -rf cache && cp -a "$LIVE" cache
  git add -A output cache
  git commit -qm "Data: verified website discovery batch" && push
  grep -q " 0 to try this run" /tmp/claude-0/discover_batch.log && break
done
echo DISCOVERY DONE
