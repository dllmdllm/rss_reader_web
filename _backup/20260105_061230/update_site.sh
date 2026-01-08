#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$ROOT_DIR/generate_site.py" --lookback-hours 12 --refresh-seconds 600

if [ -d "$ROOT_DIR/.git" ]; then
  if ! git -C "$ROOT_DIR" config --global --get-all safe.directory | grep -qx "$ROOT_DIR"; then
    git -C "$ROOT_DIR" config --global --add safe.directory "$ROOT_DIR" >/dev/null 2>&1 || true
  fi
  git -C "$ROOT_DIR" add index.html info.html images || true
  if ! git -C "$ROOT_DIR" diff --cached --quiet; then
    git -C "$ROOT_DIR" commit -m "Update site $(date +'%Y-%m-%d %H:%M')"
    git -C "$ROOT_DIR" push
  fi
fi
