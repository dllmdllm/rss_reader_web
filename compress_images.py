#!/usr/bin/env python3
import os
import json
import tempfile
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

PROJECT_ROOT = os.path.dirname(__file__)
IMAGES_DIR = os.path.join(PROJECT_ROOT, "images")
INDEX_PATH = os.path.join(PROJECT_ROOT, "index.html")
IMAGE_CACHE_PATH = os.path.join(PROJECT_ROOT, "data", "image_cache.json")
COMPRESS_CACHE_PATH = os.path.join(PROJECT_ROOT, "data", "compress_cache.json")

MAX_WIDTH = 720
TARGET_BYTES = 200 * 1024
QUALITY_JPG = 52
QUALITY_WEBP = 48
MIN_WIDTH = 480


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def save_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    if os.path.exists(tmp):
        os.replace(tmp, path)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)


def save_with_quality(img_obj: Image.Image, q: int, ext: str) -> str:
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
    os.close(tmp_fd)
    if ext in (".jpg", ".jpeg"):
        if img_obj.mode not in ("RGB", "L"):
            img_obj = img_obj.convert("RGB")
        img_obj.save(tmp_path, format="JPEG", quality=q, optimize=True, progressive=True)
    elif ext == ".webp":
        if img_obj.mode not in ("RGB", "RGBA"):
            img_obj = img_obj.convert("RGB")
        img_obj.save(tmp_path, format="WEBP", quality=q, method=6)
    else:
        if img_obj.mode not in ("RGB", "RGBA"):
            img_obj = img_obj.convert("RGB")
        img_obj.save(tmp_path, format="WEBP", quality=q, method=6)
    return tmp_path


def compress_image(path: str) -> None:
    ext = os.path.splitext(path)[1].lower()
    tmp_path = ""
    try:
        img = Image.open(path)
        img.load()
        if img.width > MAX_WIDTH:
            new_h = max(1, int(img.height * (MAX_WIDTH / img.width)))
            img = img.resize((MAX_WIDTH, new_h), Image.LANCZOS)
        if ext in (".gif", ".png"):
            ext = ".webp"
        q = QUALITY_JPG if ext in (".jpg", ".jpeg") else QUALITY_WEBP
        tmp_path = save_with_quality(img, q, ext)
        if ext in (".jpg", ".jpeg", ".webp"):
            while os.path.getsize(tmp_path) > TARGET_BYTES and q > 30:
                os.remove(tmp_path)
                q -= 5
                tmp_path = save_with_quality(img, q, ext)
            cur = img
            while os.path.getsize(tmp_path) > TARGET_BYTES and cur.width > MIN_WIDTH:
                new_w = max(MIN_WIDTH, int(cur.width * 0.85))
                new_h = max(1, int(cur.height * (new_w / cur.width)))
                cur = cur.resize((new_w, new_h), Image.LANCZOS)
                os.remove(tmp_path)
                tmp_path = save_with_quality(cur, q, ext)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def main() -> int:
    if not os.path.isdir(IMAGES_DIR):
        return 0
    if not os.path.isfile(INDEX_PATH):
        return 0

    with open(INDEX_PATH, "r", encoding="utf-8", errors="ignore") as handle:
        html_text = handle.read()
    cache = load_json(IMAGE_CACHE_PATH)
    compress_cache = load_json(COMPRESS_CACHE_PATH)

    for name in os.listdir(IMAGES_DIR):
        path = os.path.join(IMAGES_DIR, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".jpg", ".jpeg", ".webp", ".png", ".gif"):
            continue
        try:
            stat = os.stat(path)
            sig = f"{stat.st_size}:{int(stat.st_mtime)}"
        except Exception:
            sig = ""
        if name in compress_cache and compress_cache.get(name) == sig:
            continue
        compress_image(path)
        base = os.path.splitext(name)[0]
        base_no_ok = base[:-3] if base.endswith("_OK") else base
        out_ext = ext
        if ext == ".png":
            out_ext = ".webp"
        new_name = f"{base_no_ok}_OK{out_ext}"
        new_path = os.path.join(IMAGES_DIR, new_name)
        try:
            if os.path.exists(new_path):
                os.remove(new_path)
            os.replace(path, new_path)
        except Exception:
            continue
        html_text = html_text.replace(f"images/{name}", f"images/{new_name}")
        if base_no_ok != base:
            html_text = html_text.replace(f"images/{base_no_ok}{out_ext}", f"images/{new_name}")
        for url, meta in list(cache.items()):
            if meta.get("path") in (name, f"{base_no_ok}{out_ext}"):
                meta["path"] = new_name
                cache[url] = meta
        if sig:
            compress_cache[new_name] = sig

    with open(INDEX_PATH, "w", encoding="utf-8") as handle:
        handle.write(html_text)
    save_json(IMAGE_CACHE_PATH, cache)
    save_json(COMPRESS_CACHE_PATH, compress_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
