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

quality_jpg = 60
quality_webp = 58
max_width = 900
target_bytes = 300 * 1024

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
    if ext not in {".jpg", ".jpeg", ".webp", ".png"}:
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
        if ext == ".png":
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
            os.close(tmp_fd)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(tmp_path, format="PNG", optimize=True)
        else:
            tmp_path = save_with_quality(img, q, ext)
        while os.path.getsize(tmp_path) > target_bytes and q > 40:
            os.remove(tmp_path)
            q -= 5
            tmp_path = save_with_quality(img, q, ext)
        # iterative downscale until under target or too small
        cur_img = img
        while os.path.getsize(tmp_path) > target_bytes and cur_img.width > 640:
            new_w = max(640, int(cur_img.width * 0.85))
            new_h = max(1, int(cur_img.height * (new_w / cur_img.width)))
            cur_img = cur_img.resize((new_w, new_h), Image.LANCZOS)
            os.remove(tmp_path)
            tmp_path = save_with_quality(cur_img, q, ext)

        os.replace(tmp_path, path)
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

# Cleanup: remove images no longer referenced by index.html
CLEAN_MARK="$ROOT_DIR/data/last_image_cleanup.txt"
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
