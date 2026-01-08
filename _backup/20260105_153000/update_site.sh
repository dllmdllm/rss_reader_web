#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$ROOT_DIR/generate_site.py" --lookback-hours 6 --refresh-seconds 600

# Daily cleanup: remove images no longer referenced by index.html
CLEAN_MARK="$ROOT_DIR/data/last_image_cleanup.txt"
NOW_EPOCH="$(date +%s)"
LAST_EPOCH=0
if [ -f "$CLEAN_MARK" ]; then
  LAST_EPOCH="$(cat "$CLEAN_MARK" 2>/dev/null || echo 0)"
fi
if [ $((NOW_EPOCH - LAST_EPOCH)) -ge 86400 ]; then
  ROOT_DIR="$ROOT_DIR" python3 - <<'PY'
import os, re, time

root = os.environ.get("ROOT_DIR") or os.getcwd()
index_path = os.path.join(root, "index.html")
images_dir = os.path.join(root, "images")
mark_path = os.path.join(root, "data", "last_image_cleanup.txt")

if not os.path.isfile(index_path) or not os.path.isdir(images_dir):
    with open(mark_path, "w", encoding="utf-8") as f:
        f.write(str(int(time.time())))
    raise SystemExit(0)

with open(index_path, "r", encoding="utf-8", errors="ignore") as f:
    html = f.read()

used = set()
for m in re.finditer(r'images/([^"\\?]+)', html):
    used.add(m.group(1))

for name in os.listdir(images_dir):
    path = os.path.join(images_dir, name)
    if not os.path.isfile(path):
        continue
    if name not in used:
        try:
            os.remove(path)
        except OSError:
            pass

os.makedirs(os.path.dirname(mark_path), exist_ok=True)
with open(mark_path, "w", encoding="utf-8") as f:
    f.write(str(int(time.time())))
PY
fi

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
