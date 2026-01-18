
import html
import re
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime

def strip_html(value: str) -> str:
    """Removes HTML tags and cleans up whitespace."""
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()

def normalize_link(link: str) -> str:
    """Cleans up link URLs."""
    if not link:
        return ""
    link = html.unescape(link).strip()
    if "<a" in link and "href" in link:
        m = re.search(r'href=["\']([^"\']+)["\']', link)
        if m:
            return m.group(1).strip()
    if '"' in link:
        link = link.split('"', 1)[0].strip()
    if " " in link:
        link = link.split(" ", 1)[0].strip()
    return link

def normalize_image_url(base_url: str, raw_url: str) -> str:
    if not raw_url:
        return ""
    raw_url = raw_url.strip()
    if raw_url.startswith("//"):
        return "https:" + raw_url
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url
    return urllib.parse.urljoin(base_url, raw_url)

def parse_pub_date(text: str) -> datetime | None:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
        return dt
    except Exception:
        return None

def is_generic_image(url: str, source_url: str | None = None) -> bool:
    """Detects placeholder or generic images to ignore."""
    if not url:
        return True
    lowered = url.lower()
    keys = [
        "logo", "default", "placeholder", "site-logo", "share", "social",
        "/seo/", "image/seo", "/res/v3/image/seo", "grey.gif", "blank.gif", 
        "transparent.gif"
    ]
    if source_url and "mingpao.com" in source_url:
        keys = [k for k in keys if k not in ("image/seo", "/res/v3/image/seo")]
    return any(key in lowered for key in keys)


# --- Text Cleaning Utils ---

CSS_BLOCK_START_RE = re.compile(r"^[\w\.#,\s-]+\s*\{")
CSS_PROP_RE = re.compile(r"^[\w-]+\s*:\s*[^;]+;?$")

def clean_content_text(text: str) -> str:
    if not text:
        return text
    lines = []
    in_css_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        normalized = re.sub(r"[↓▼\s]+", "", line)
        if (
            "相關字詞" in line
            or "編輯推介" in line
            or "熱門HOTPICK" in line
            or "報道詳情" in line
            or "相關文章" in line
            or "相關文章︳" in line
            or "相關新聞" in line
            or "相關閱讀" in line
            or "相關閲讀" in line
            or "延伸閱讀" in line
            or "來源網址" in line
            or "立即下載星島頭條App" in line
            or "星島頭條App" in line
            or "即睇減息部署" in line
            or "同場加映" in line
            or "[email protected]" in line
            or "最Hit" in line
            or line.strip() in ("有片", "（有片）", "有片！")
            or ("下載" in line and "星島" in line and "App" in line)
            or ("即睇" in line and "部署" in line)
            or re.search(r"[↓▼].+?[↓▼]", line)
            or "立即下載星島頭條App" in normalized
            or "即睇減息部署" in normalized
            or (
                re.search(r"\\bEmail\\b", line, re.I)
                and ("驗樓" in line or "新盤" in line or "裝修" in line)
            )
            or (("@" in line) and ("驗樓" in line or "新盤" in line or "裝修" in line))
            or "上車驗樓" in line
        ):
            continue
        if line.startswith("相關文章") or line.startswith("相關閱讀") or line.startswith("相關新聞") or line.startswith("延伸閱讀"):
            continue
        if line.startswith("相關閲讀"):
            continue
        if line.startswith("來源網址"):
            continue
        if CSS_BLOCK_START_RE.match(line):
            in_css_block = True
            continue
        if in_css_block:
            if "}" in line:
                in_css_block = False
            continue
        if CSS_PROP_RE.match(line) and not re.search(r"[\u4e00-\u9fff]", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()

import json
import os

def ensure_dirs():
    """Ensures that necessary directories exist."""
    from .config import DATA_DIR, CACHE_DIR, IMAGES_DIR
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

def load_json(path: str) -> dict | list:
    """Loads a JSON file safely."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path: str, data: dict | list):
    """Saves data to a JSON file safely."""
    try:
        tmp_path = path + ".tmp"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving JSON to {path}: {e}")

