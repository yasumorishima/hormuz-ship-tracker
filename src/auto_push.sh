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

# 1. Generate snapshot
echo "Generating snapshot..."
python /app/src/snapshot.py
echo ""

SNAPSHOT="/app/data/snapshot.png"
STATS="/app/data/snapshot_stats.txt"
DEST_IMG="/repo/docs/snapshot_latest.png"
DEST_STATS="/repo/docs/snapshot_stats.txt"

if [ ! -f "$SNAPSHOT" ]; then
    echo "ERROR: snapshot.png not generated"
    exit 1
fi

# 2. Check if image changed (compare SHA256)
NEW_HASH=$(sha256sum "$SNAPSHOT" | cut -d' ' -f1)
if [ -f "$DEST_IMG" ]; then
    OLD_HASH=$(sha256sum "$DEST_IMG" | cut -d' ' -f1)
else
    OLD_HASH=""
fi

if [ "$NEW_HASH" = "$OLD_HASH" ]; then
    echo "No change in snapshot — skipping push"
    exit 0
fi

echo "Snapshot changed (old=$OLD_HASH, new=$NEW_HASH)"

# 3. Copy files to repo
cp "$SNAPSHOT" "$DEST_IMG"
cp "$STATS" "$DEST_STATS"

# 4. Configure git
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

git add docs/snapshot_latest.png docs/snapshot_stats.txt
git commit -m "snapshot: ${VESSEL_COUNT} vessels at ${TIMESTAMP}" || {
    echo "Nothing to commit"
    exit 0
}

echo "Pushing to GitHub..."
git push origin HEAD
echo "Push complete."
