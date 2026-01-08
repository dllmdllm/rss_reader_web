#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$ROOT_DIR/generate_site.py" --lookback-hours 6 --refresh-seconds 600

# Optional lossy compression for images (uses venv Pillow if available)
VENV_PY="/mnt/c/Users/Nary/.venv/rss_reader_web/bin/python"
if [ -x "$VENV_PY" ]; then
  ROOT_DIR="$ROOT_DIR" "$VENV_PY" - <<'PY'
import os
import tempfile
from PIL import Image

root = os.path.abspath(os.environ.get("ROOT_DIR") or os.getcwd())
images_dir = os.path.join(root, "images")
if not os.path.isdir(images_dir):
    raise SystemExit(0)

quality_jpg = 70
quality_webp = 68
max_width = 900
target_bytes = 500 * 1024

def total_size():
    total = 0
    for name in os.listdir(images_dir):
        path = os.path.join(images_dir, name)
        if os.path.isfile(path):
            total += os.path.getsize(path)
    return total

before = total_size()

for name in os.listdir(images_dir):
    path = os.path.join(images_dir, name)
    if not os.path.isfile(path):
        continue
    ext = os.path.splitext(name)[1].lower()
    if ext not in {".jpg", ".jpeg", ".webp"}:
        continue
    try:
        img = Image.open(path)
        img.load()
        if img.width > max_width:
            new_h = max(1, int(img.height * (max_width / img.width)))
            img = img.resize((max_width, new_h), Image.LANCZOS)

        def save_with_quality(img_obj, q, suffix):
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(tmp_fd)
            if suffix in (".jpg", ".jpeg"):
                if img_obj.mode not in ("RGB", "L"):
                    img_obj = img_obj.convert("RGB")
                img_obj.save(tmp_path, format="JPEG", quality=q, optimize=True, progressive=True)
            else:
                if img_obj.mode not in ("RGB", "RGBA"):
                    img_obj = img_obj.convert("RGB")
                img_obj.save(tmp_path, format="WEBP", quality=q, method=6)
            return tmp_path

        q = quality_jpg if ext in {".jpg", ".jpeg"} else quality_webp
        tmp_path = save_with_quality(img, q, ext)
        while os.path.getsize(tmp_path) > target_bytes and q > 45:
            os.remove(tmp_path)
            q -= 5
            tmp_path = save_with_quality(img, q, ext)
        if os.path.getsize(tmp_path) > target_bytes and img.width > 640:
            # final fallback: shrink a bit more
            new_w = max(640, int(img.width * 0.85))
            new_h = max(1, int(img.height * (new_w / img.width)))
            img2 = img.resize((new_w, new_h), Image.LANCZOS)
            os.remove(tmp_path)
            tmp_path = save_with_quality(img2, q, ext)

        if os.path.getsize(tmp_path) < os.path.getsize(path):
            os.replace(tmp_path, path)
        else:
            os.remove(tmp_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

after = total_size()
if before > 0:
    diff = before - after
    pct = diff / before * 100
    print(f"image-compress: before={before} after={after} saved={diff} ({pct:.2f}%)")
PY
fi

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
