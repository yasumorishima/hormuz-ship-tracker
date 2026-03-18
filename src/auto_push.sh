#!/bin/bash
# auto_push.sh — Generate a map snapshot and push to GitHub if changed.
#
# Requires:
#   GITHUB_TOKEN  — Personal access token with repo push permission
#   GITHUB_REPO   — e.g. "yasumorishima/hormuz-ship-tracker"
#
# Runs inside the Docker container. The host repo is mounted at /repo.
set -euo pipefail

echo "=== auto_push.sh $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

# 1. Generate snapshot + heatmap
echo "Generating snapshot..."
python /app/src/snapshot.py
echo ""

echo "Generating heatmap..."
python /app/src/heatmap.py --hours 0 --filename heatmap.png
echo ""

echo "Generating stats report..."
python /app/src/stats_report.py --db /app/data/ais.db --output /repo/docs/STATS.md
echo ""

SNAPSHOT="/app/data/snapshot.png"
STATS="/app/data/snapshot_stats.txt"
HEATMAP="/app/data/heatmap.png"
DEST_IMG="/repo/docs/snapshot_latest.png"
DEST_STATS="/repo/docs/snapshot_stats.txt"
DEST_HEATMAP="/repo/docs/heatmap.png"

if [ ! -f "$SNAPSHOT" ]; then
    echo "ERROR: snapshot.png not generated"
    exit 1
fi

# 2. Check if any image changed (compare SHA256)
CHANGED=false

NEW_HASH=$(sha256sum "$SNAPSHOT" | cut -d' ' -f1)
OLD_HASH=""
[ -f "$DEST_IMG" ] && OLD_HASH=$(sha256sum "$DEST_IMG" | cut -d' ' -f1)
[ "$NEW_HASH" != "$OLD_HASH" ] && CHANGED=true

if [ -f "$HEATMAP" ]; then
    NEW_HM_HASH=$(sha256sum "$HEATMAP" | cut -d' ' -f1)
    OLD_HM_HASH=""
    [ -f "$DEST_HEATMAP" ] && OLD_HM_HASH=$(sha256sum "$DEST_HEATMAP" | cut -d' ' -f1)
    [ "$NEW_HM_HASH" != "$OLD_HM_HASH" ] && CHANGED=true
fi

# STATS.md is always regenerated (text changes every cycle)
CHANGED=true

if [ "$CHANGED" = false ]; then
    echo "No changes — skipping push"
    exit 0
fi

echo "Changes detected — updating docs/"

# 3. Copy files to repo
cp "$SNAPSHOT" "$DEST_IMG"
cp "$STATS" "$DEST_STATS"
[ -f "$HEATMAP" ] && cp "$HEATMAP" "$DEST_HEATMAP"

# 4. Configure git
git config --global --add safe.directory /repo
cd /repo
git config user.name "hormuz-bot"
git config user.email "hormuz-bot@users.noreply.github.com"

# Set up credentials for push
if [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_REPO:-}" ]; then
    git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
fi

# 5. Commit and push
TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M UTC')
VESSEL_COUNT=$(grep -oP 'Active vessels.*?: \K[0-9]+' "$STATS" 2>/dev/null || echo "?")

git add docs/snapshot_latest.png docs/snapshot_stats.txt docs/heatmap.png docs/STATS.md
git commit -m "snapshot: ${VESSEL_COUNT} vessels at ${TIMESTAMP}" || {
    echo "Nothing to commit"
    exit 0
}

echo "Pushing to GitHub..."
git push origin HEAD
echo "Push complete."
