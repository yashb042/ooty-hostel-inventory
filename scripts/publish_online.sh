#!/usr/bin/env bash
# Publish hostel-parsing to the public GitHub Pages repo (cloud-only; no local automation).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_NAME="${REPO_NAME:-ooty-hostel-inventory}"
DEPLOY_DIR="$(mktemp -d)"

cleanup() { rm -rf "$DEPLOY_DIR"; }
trap cleanup EXIT

rsync -a \
  --exclude '.venv' \
  --exclude 'logs' \
  --exclude 'site' \
  --exclude 'data/inventory.db' \
  --exclude 'data/.collector.lock.d' \
  --exclude 'data/.dashboard.lock.d' \
  --exclude '__pycache__' \
  "$ROOT/" "$DEPLOY_DIR/"

mkdir -p "$DEPLOY_DIR/.github/workflows"
cp "$ROOT/.github/workflows/hostel-inventory.yml" "$DEPLOY_DIR/.github/workflows/hostel-inventory.yml"

cd "$DEPLOY_DIR"
git init -b main
git add .
git commit -m "feat: schema v2 with normalized inventory tables and redesigned dashboard"

if gh repo view "yashb042/$REPO_NAME" >/dev/null 2>&1; then
  git remote add origin "https://github.com/yashb042/$REPO_NAME.git"
  git push -u origin main --force
else
  gh repo create "yashb042/$REPO_NAME" --public --source=. --remote=origin --push \
    --description "Ooty & Kodaikanal hostel inventory — daily parsing + public dashboard"
fi

gh api -X POST "repos/yashb042/$REPO_NAME/pages" -f build_type=workflow 2>/dev/null || \
  gh api -X PUT "repos/yashb042/$REPO_NAME/pages" -f build_type=workflow 2>/dev/null || true

gh workflow run hostel-inventory.yml --repo "yashb042/$REPO_NAME" -f quick_test=true

echo ""
echo "Public repo: https://github.com/yashb042/$REPO_NAME"
echo "Dashboard:   https://yashb042.github.io/$REPO_NAME/ (live after workflow completes)"
