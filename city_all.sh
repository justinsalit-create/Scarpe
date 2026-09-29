#!/usr/bin/env bash
# Full run for any city in cities/<slug>.json: map data + website crawl, then verified website
# discovery in batches, committing after each batch. Resumable (cache in ~/.foodcontacts-cache/<slug>,
# snapshotted to cache/<slug>/ in the repo).   Usage: ./city_all.sh bangkok
set -u
cd "$(dirname "$0")"
CITY=$1
LIVE=${FOODCONTACTS_CACHE:-$HOME/.foodcontacts-cache}/$CITY
[ -d "$LIVE" ] || { mkdir -p "$(dirname "$LIVE")"; [ -d "cache/$CITY" ] && cp -a "cache/$CITY" "$LIVE"; }
BRANCH=$(git rev-parse --abbrev-ref HEAD)
LOG=/tmp/claude-0/${CITY}_batch.log
mkdir -p /tmp/claude-0
push() { for d in 2 4 8 16; do git push -q -u origin "$BRANCH" && return; sleep $d; done; }
snapshot() {
  mkdir -p cache && rm -rf "cache/$CITY" && cp -a "$LIVE" "cache/$CITY"
  git add -A "output/$CITY" "cache/$CITY"
  git commit -qm "Data ($CITY): $1" && push
}
if [ ! -f "output/$CITY/${CITY}_food_emails.csv" ]; then   # first pass (skipped when resuming)
  python3 -m londonfood.city "$CITY" --workers 24 2>&1 | tee "$LOG"
  snapshot "map data and website crawl"
fi
while true; do
  python3 -m londonfood.city "$CITY" --workers 24 --discover --discover-limit 6000 2>&1 | tee "$LOG"
  snapshot "verified website discovery batch"
  grep -q " 0 to try this run" "$LOG" && grep -q "Districts not downloaded yet: 0" "$LOG" && break
  grep -q "Districts not downloaded yet: 0" "$LOG" || sleep 120   # give the map server a breather
done
python3 tools/make_pdfs.py "$CITY" && git add "output/$CITY/pdf" && git commit -qm "PDFs ($CITY)" && push
echo "CITY DONE: $CITY"
