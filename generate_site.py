#!/usr/bin/env python3
import argparse
import base64
import hashlib
import difflib
import html
import json
import os
import random
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit, quote
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo
try:
    from opencc import OpenCC
except Exception:
    OpenCC = None
try:
    import stanza
except Exception:
    stanza = None

STANZA_NLP = None


DEFAULT_URLS = ",".join(
    [
        "https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_clocal.xml",
        "https://news.mingpao.com/rss/ins/all.xml",
        "https://news.mingpao.com/rss/ins/s00004.xml",
        "https://news.mingpao.com/rss/ins/s00005.xml",
        "https://news.mingpao.com/rss/ins/s00007.xml",
        "https://rss.cnbeta.com.tw/",
        "https://hk.on.cc/hk/news/index.html",
        "https://hk.on.cc/hk/intnews/index.html",
        "https://hk.on.cc/hk/entertainment/index.html",
        "https://www.stheadline.com/rss",
        "https://www.stheadline.com/realtime-china/%E5%8D%B3%E6%99%82%E4%B8%AD%E5%9C%8B",
        "https://www.stheadline.com/realtime-world/%E5%8D%B3%E6%99%82%E5%9C%8B%E9%9A%9B",
        "https://www.stheadline.com/entertainment",
        "https://www.hk01.com",
        "https://www.hk01.com/zone/2/%E5%A8%9B%E6%A8%82",
        "https://www.hk01.com/channel/19/%E5%8D%B3%E6%99%82%E5%9C%8B%E9%9A%9B",
        "https://www.hk01.com/zone/5/%E4%B8%AD%E5%9C%8B",
    ]
)
DEFAULT_LOOKBACK_HOURS = 6
DEFAULT_REFRESH_SECONDS = 600
DEFAULT_MAX_ITEMS = 200
DEFAULT_THREADS = 4
CNBETA_LIMIT = 50
MIXED_MODE = True
ONCC_LIMIT = 50
HK01_LIMIT = 20
SINGTAO_ENT_LIMIT = 50
HTTP_TIMEOUT = 18
SINGTAO_TIMEOUT = 12

PROJECT_ROOT = os.path.dirname(__file__)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SITE_DIR = PROJECT_ROOT
IMAGES_DIR = os.path.join(SITE_DIR, "images")
FEED_CACHE_PATH = os.path.join(DATA_DIR, "feed_cache.json")
FULLTEXT_CACHE_PATH = os.path.join(DATA_DIR, "fulltext_cache.json")
IMAGE_CACHE_PATH = os.path.join(DATA_DIR, "image_cache.json")
FULLHTML_CACHE_PATH = os.path.join(DATA_DIR, "fullhtml_cache.json")

FULLTEXT_CACHE_TTL = 6 * 60 * 60
IMAGE_CACHE_TTL = 24 * 60 * 60
FULLHTML_CACHE_TTL = 6 * 60 * 60
CACHE_GC_TTL = 7 * 24 * 60 * 60


@dataclass
class Item:
    title: str
    link: str
    pub_dt: datetime | None
    pub_text: str
    source: str
    category: str
    summary: str
    rss_image: str
    extra_images: list[str] = field(default_factory=list)
    image_count: int = 0


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SITE_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def gc_cache(cache: dict, ttl_seconds: int) -> dict:
    now = time.time()
    cleaned = {}
    for key, value in (cache or {}).items():
        try:
            ts = float(value.get("timestamp", 0) or 0)
        except Exception:
            ts = 0
        if not ts or now - ts <= ttl_seconds:
            cleaned[key] = value
    return cleaned


def get_trad_converter():
    if OpenCC is None:
        return None
    try:
        return OpenCC("s2hk")
    except Exception:
        return None


TRAD_CONVERTER = get_trad_converter()


def to_trad(text: str) -> str:
    if not text:
        return text
    if TRAD_CONVERTER is None:
        return text
    try:
        converted = TRAD_CONVERTER.convert(text)
        replacements = {
            "髮布": "發布",
            "發佈": "發布",
        }
        for src, dst in replacements.items():
            converted = converted.replace(src, dst)
        return converted
    except Exception:
        return text


def to_trad_if_cnbeta(source_or_url: str, text: str) -> str:
    if not text:
        return text
    if "cnbeta" in (source_or_url or ""):
        return to_trad(text)
    return text

def save_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    if os.path.exists(tmp):
        os.replace(tmp, path)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)


def fetch_with_cache(url: str, cache: dict) -> tuple[bytes, dict]:
    headers = {"User-Agent": "Mozilla/5.0 (RSS Reader)"}
    entry = cache.get(url, {})
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        headers["If-Modified-Since"] = entry["last_modified"]
    req = urllib.request.Request(url, headers=headers)
    try:
        if "rss.cnbeta.com.tw" in url:
            import ssl

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ctx) as resp:
                payload = resp.read()
                meta = {
                    "etag": resp.headers.get("ETag") or "",
                    "last_modified": resp.headers.get("Last-Modified") or "",
                    "timestamp": time.time(),
                }
                return payload, meta
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = resp.read()
            meta = {
                "etag": resp.headers.get("ETag") or "",
                "last_modified": resp.headers.get("Last-Modified") or "",
                "timestamp": time.time(),
            }
            return payload, meta
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and entry.get("payload_b64"):
            return base64.b64decode(entry["payload_b64"]), entry
        if entry.get("payload_b64"):
            return base64.b64decode(entry["payload_b64"]), entry
        raise
    except Exception:
        if entry.get("payload_b64"):
            return base64.b64decode(entry["payload_b64"]), entry
        return b"", entry


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


def find_text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    if child is None:
        return ""
    return (child.text or "").strip()


def strip_html(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def clean_content_text(text: str) -> str:
    if not text:
        return text
    lines = []
    in_css_block = False
    css_block_start = re.compile(r"^[\w\.#,\s-]+\s*\{")
    css_prop = re.compile(r"^[\w-]+\s*:\s*[^;]+;?$")
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
            or "延伸閱讀" in line
            or "立即下載星島頭條App" in line
            or "星島頭條App" in line
            or "即睇減息部署" in line
            or "同場加映" in line
            or "[email protected]" in line
            or "最Hit" in line
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
        if css_block_start.match(line):
            in_css_block = True
            continue
        if in_css_block:
            if "}" in line:
                in_css_block = False
            continue
        if css_prop.match(line) and not re.search(r"[\u4e00-\u9fff]", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def clean_html_fragment(fragment: str, base_url: str, image_cache: dict | None = None) -> str:
    if not fragment:
        return ""
    try:
        from lxml import html as lxml_html
    except Exception:
        return fragment
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
        for node in root.xpath(".//script | .//style | .//noscript | .//video | .//iframe"):
            node.getparent().remove(node)
        if "stheadline.com" in base_url:
            for node in root.xpath(".//ad"):
                node.getparent().remove(node)
        else:
            for node in root.xpath(".//ad | .//*[starts-with(name(),'gallery-')]"):
                node.getparent().remove(node)
        if "mingpao.com" not in base_url:
            for node in root.xpath(
                ".//*[contains(@class,'related') or contains(@class,'keyword') "
                "or contains(@class,'share') or contains(@class,'social') "
                "or contains(@class,'breadcrumb')]"
            ):
                node.getparent().remove(node)
        else:
            for node in root.xpath(".//*[contains(text(),'相關字詞') or contains(text(),'報道詳情') or contains(text(),'編輯推介') or contains(text(),'熱門HOTPICK')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
        if "stheadline.com" in base_url:
            for node in root.xpath(".//*[contains(text(),'同場加映') or contains(text(),'星島頭條App') or contains(text(),'即睇減息部署') or contains(text(),'立即下載') or contains(text(),'相關閱讀') or contains(text(),'延伸閱讀')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'相關新聞') or contains(text(),'相關閱讀') or contains(text(),'延伸閱讀')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'相關文章')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'最Hit')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(@class,'article-title')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(@class,'time') or contains(text(),'更新時間') or contains(text(),'發佈時間')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'下載') and contains(text(),'App')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'上車驗樓') or (contains(text(),'Email') and (contains(text(),'驗樓') or contains(text(),'新盤') or contains(text(),'裝修')))]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(
                ".//*[contains(@class,'hit-articles') or contains(@class,'hit-block') or contains(@class,'hit-img') or contains(@class,'related') or contains(@class,'recommend')]"
            ):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for link in root.xpath(".//a"):
                # keep images, but remove text links
                if link.xpath(".//img"):
                    link.drop_tag()
                    continue
                parent = link.getparent()
                if parent is not None and parent.tag in ("p", "li", "div"):
                    parent.remove(link)
                    if (parent.text_content() or "").strip() == "":
                        gp = parent.getparent()
                        if gp is not None:
                            gp.remove(parent)
                else:
                    link.drop_tag()
        for img in root.xpath(".//img"):
            src = img.get("src")
            if not src:
                srcset = img.get("srcset") or img.get("data-srcset")
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            if not src:
                src = img.get("data-src") or img.get("data-original")
            if src:
                normalized = normalize_image_url(base_url, src)
                img.set("src", normalized)
        if "stheadline.com" in base_url:
            for img in root.xpath(".//img[@src]"):
                src = img.get("src") or ""
                if "sthlstatic.com/sthl/assets/icons" in src or "sthlstatic.com/sthl/assets/images/logo" in src:
                    img.drop_tag()
        if image_cache is not None:
            for img in root.xpath(".//img[@src]"):
                src = img.get("src")
                if not src:
                    continue
                local_name = download_image(src, image_cache, base_url)
                if local_name:
                    img.set("src", f"images/{local_name}")
                img.set("loading", "lazy")
                img.set("decoding", "async")
        if "cnbeta.com.tw" in base_url:
            imgs = root.xpath(".//img")
            if len(imgs) > 1:
                imgs[0].drop_tag()
        if "stheadline.com" in base_url:
            imgs = list(root.xpath(".//img"))
            seen_src: set[str] = set()
            if len(imgs) > 1:
                first_src = imgs[0].get("src") or ""
                if first_src:
                    norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", first_src)
                    norm = norm.split("?")[0].split("/")[-1]
                    seen_src.add(norm)
                imgs[0].drop_tag()
            kept = 0
            for img in imgs[1:]:
                src = img.get("src") or ""
                if src:
                    norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", src)
                    norm = norm.split("?")[0].split("/")[-1]
                    if norm in seen_src:
                        img.drop_tag()
                        continue
                    seen_src.add(norm)
                    kept += 1
                    if kept > 20:
                        img.drop_tag()
                        continue
        for link in root.xpath(".//a[@href]"):
            href = normalize_image_url(base_url, link.get("href"))
            if not re.match(r"^https?://", href):
                link.drop_tag()
                continue
            link.set("href", href)
            link.set("target", "_blank")
            link.set("rel", "noopener")
        if "hk01.com" in base_url:
            for link in root.xpath(".//a"):
                link.drop_tag()
            for br in root.xpath(".//br"):
                br.drop_tag()
            for node in root.xpath(".//p | .//div | .//section | .//span"):
                if node.xpath(".//img"):
                    continue
                if (node.text_content() or "").strip() == "":
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
        if "cnbeta" in base_url:
            for node in root.iter():
                if node.text:
                    node.text = to_trad(node.text)
                if node.tail:
                    node.tail = to_trad(node.tail)
        html_text = "".join(lxml_html.tostring(child, encoding="unicode") for child in root)
        return html_text.strip()
    except Exception:
        return fragment


def should_use_fulltext(summary: str, fulltext: str) -> bool:
    if not fulltext:
        return False
    if not summary:
        return True
    if len(fulltext) >= len(summary) + 10:
        return True
    if fulltext.count("\n") > summary.count("\n"):
        return True
    if fulltext.count("。") > summary.count("。"):
        return True
    if summary in fulltext and len(fulltext) > len(summary):
        return True
    return False


def normalize_image_url(base_url: str, raw_url: str) -> str:
    if not raw_url:
        return ""
    raw_url = raw_url.strip()
    if raw_url.startswith("//"):
        return "https:" + raw_url
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url
    return urljoin(base_url, raw_url)


def safe_fetch_url(url: str) -> str:
    if not url:
        return url
    try:
        url.encode("ascii")
        return url
    except Exception:
        parts = urlsplit(url)
        path = quote(parts.path)
        query = quote(parts.query, safe="=&%")
        return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def guess_image_ext(url: str, content_type: str) -> str:
    if content_type:
        if "png" in content_type:
            return ".png"
        if "webp" in content_type:
            return ".webp"
        if "gif" in content_type:
            return ".gif"
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
    path = urlparse(url).path
    for ext in (".png", ".webp", ".gif", ".jpg", ".jpeg"):
        if path.lower().endswith(ext):
            return ext
    return ".jpg"


def get_image_prefix(referer: str | None) -> str:
    ref = (referer or "").lower()
    if "hk01" in ref:
        return "hk01_"
    if "on.cc" in ref or "oncc" in ref:
        return "oncc_"
    if "rthk" in ref:
        return "rthk_"
    if "stheadline" in ref or "singtao" in ref:
        return "singtao_"
    if "mingpao" in ref:
        return "mingpao_"
    if "cnbeta" in ref:
        return "cnbeta_"
    return ""


def download_image(url: str, cache: dict, referer: str | None = None, lock: Optional[threading.Lock] = None) -> str:
    if not url:
        return ""
    now = time.time()
    prefix = get_image_prefix(referer or url)
    if lock:
        with lock:
            entry = cache.get(url, {})
    else:
        entry = cache.get(url, {})
    cached_path = entry.get("path", "")
    cached_ts = float(entry.get("timestamp", 0) or 0)
    if cached_path and (now - cached_ts) <= IMAGE_CACHE_TTL:
        if os.path.exists(os.path.join(IMAGES_DIR, cached_path)):
            if prefix and not cached_path.startswith(prefix):
                new_name = f"{prefix}{cached_path}"
                try:
                    os.replace(
                        os.path.join(IMAGES_DIR, cached_path),
                        os.path.join(IMAGES_DIR, new_name),
                    )
                    if lock:
                        with lock:
                            cache[url] = {"path": new_name, "timestamp": cached_ts}
                    else:
                        cache[url] = {"path": new_name, "timestamp": cached_ts}
                    return new_name
                except Exception:
                    return cached_path
            return cached_path
        if lock:
            with lock:
                cache.pop(url, None)
        else:
            cache.pop(url, None)
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except Exception:
        if "cnbeta.com.tw" in url:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.cnbeta.com.tw/",
                    "Origin": "https://www.cnbeta.com.tw",
                    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                }
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    content_type = resp.headers.get("Content-Type", "")
            except Exception:
                return ""
        else:
            return ""
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()
    ext = guess_image_ext(url, content_type)
    filename = f"{prefix}{name[:16]}{ext}"
    path = os.path.join(IMAGES_DIR, filename)
    try:
        with open(path, "wb") as handle:
            handle.write(data)
        if lock:
            with lock:
                cache[url] = {"path": filename, "timestamp": now}
        else:
            cache[url] = {"path": filename, "timestamp": now}
        return filename
    except Exception:
        return ""


def get_rss_image(item: ET.Element, base_url: str) -> str:
    for child in item:
        tag = child.tag.lower()
        if tag.endswith("enclosure"):
            url = normalize_image_url(base_url, child.attrib.get("url", "") or "")
            return "" if is_generic_image(url, base_url) else url
        if "media" in tag and tag.endswith("content"):
            url = normalize_image_url(base_url, child.attrib.get("url", "") or "")
            return "" if is_generic_image(url, base_url) else url
    desc = find_text(item, "description") or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    match = re.search(r'<img[^>]+data-src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    return ""


def get_rss_image_from_desc(desc: str, base_url: str) -> str:
    if not desc:
        return ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    match = re.search(r'<img[^>]+data-src=["\']([^"\']+)["\']', desc)
    if match:
        url = normalize_image_url(base_url, match.group(1))
        return "" if is_generic_image(url, base_url) else url
    return ""


def is_generic_image(url: str, source_url: str | None = None) -> bool:
    if not url:
        return True
    lowered = url.lower()
    keys = [
        "logo",
        "default",
        "placeholder",
        "site-logo",
        "share",
        "social",
        "/seo/",
        "image/seo",
        "/res/v3/image/seo",
    ]
    if source_url and "mingpao.com" in source_url:
        keys = [k for k in keys if k not in ("image/seo", "/res/v3/image/seo")]
    return any(key in lowered for key in keys)


def extract_fulltext_and_image(url: str, cache: dict) -> tuple[str, str]:
    if not url:
        return "", ""
    now = time.time()
    entry = cache.get(url, {})
    cached_text = entry.get("text", "")
    cached_image = entry.get("image", "")
    cached_ts = float(entry.get("timestamp", 0) or 0)
    if cached_text and (now - cached_ts) <= FULLTEXT_CACHE_TTL:
        is_mingpao = any(
            host in url
            for host in ("news.mingpao.com", "ol.mingpao.com", "finance.mingpao.com")
        )
        if not (is_mingpao and len(cached_text) < 400):
            if len(cached_text) >= 200:
                if not (is_mingpao and not cached_image):
                    return cached_text, cached_image
    try:
        from lxml import html as lxml_html
    except Exception:
        return "", ""
    fetch_url = safe_fetch_url(url)
    try:
        req = urllib.request.Request(fetch_url, headers={"User-Agent": "Mozilla/5.0"})
        timeout = SINGTAO_TIMEOUT if "stheadline.com" in fetch_url else HTTP_TIMEOUT
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return cached_text, cached_image

    if "on.cc" in url:
        text, image_url = extract_oncc_content_and_image(raw)
        if text:
            cache[url] = {"text": text, "image": image_url, "timestamp": now}
            return text, image_url

    image_url = ""
    try:
        root_full = lxml_html.fromstring(raw)
        og_image = root_full.xpath("//meta[@property='og:image']/@content")
        if og_image:
            candidate = og_image[0].strip()
            if not is_generic_image(candidate, url):
                image_url = candidate
        if not image_url:
            twitter_image = root_full.xpath("//meta[@name='twitter:image']/@content")
            if twitter_image:
                candidate = twitter_image[0].strip()
                if not is_generic_image(candidate, url):
                    image_url = candidate
        if not image_url:
            if "news.rthk.hk" in url:
                img = root_full.xpath(
                    "//div[contains(@class,'itemImage')]//img/@src | "
                    "//div[contains(@class,'itemFullText')]//img/@src | "
                    "//div[contains(@class,'itemFullText')]//img/@data-src"
                )
            elif "mingpao.com" in url:
                img = root_full.xpath(
                    "//figure//img/@data-original | "
                    "//figure//img/@src | "
                    "//div[contains(@class,'article')]//img/@data-original | "
                    "//div[contains(@class,'article')]//img/@src"
                )
            elif "stheadline.com" in url:
                img = root_full.xpath(
                    '//*[@itemprop="articleBody"]//img/@src | '
                    '//*[@itemprop="articleBody"]//img/@data-src | '
                    '//*[@itemprop="articleBody"]//img/@data-original'
                )
            else:
                img = root_full.xpath(
                    "//article//img/@src | "
                    "//article//img/@data-src | "
                    "//article//img/@data-original | "
                    "//img/@src | //img/@data-src | //img/@data-original"
                )
            if img:
                image_url = img[0].strip()
        if not image_url:
            srcset = root_full.xpath("//img/@srcset")
            if srcset:
                first = srcset[0].split(",")[0].strip().split(" ")[0]
                image_url = first
    except Exception:
        image_url = ""

    if image_url:
        image_url = normalize_image_url(url, image_url)

    text = ""
    try:
        root = lxml_html.fromstring(raw)
        if "cnbeta.com.tw" in url:
            nodes = []
        elif "news.rthk.hk" in url:
            nodes = root.xpath("//div[contains(@class,'itemFullText')]")
        elif "mingpao.com" in url:
            nodes = root.xpath(
                "//div[@id='blockcontent']//article[contains(@class,'txt4')] | "
                "//article[contains(@class,'txt4')] | "
                "//div[@id='upper'] | "
                "//div[@id='articleContent'] | "
                "//div[contains(@class,'articleContent')] | "
                "//div[contains(@class,'articlecontent')] | "
                "//div[contains(@class,'articleDetail')] | "
                "//div[contains(@class,'article')]"
            )
        elif "stheadline.com" in url:
            nodes = root.xpath('//*[@itemprop="articleBody"]')
        else:
            nodes = root.xpath("//article")
        if nodes:
            best = ""
            for node in nodes:
                ps = node.xpath(".//p")
                if ps:
                    candidate = "\n".join(
                        p.text_content().strip() for p in ps if p.text_content().strip()
                    )
                else:
                    candidate = node.text_content()
                candidate = strip_html(candidate)
                if len(candidate) > len(best):
                    best = candidate
            text = best
        if "mingpao.com" in url and text:
            if "主頁" in text and "每日明報" in text:
                text = ""
            else:
                lines = [ln for ln in text.splitlines() if ln.strip()]
                lines = [
                    ln
                    for ln in lines
                    if "相關字詞" not in ln and "編輯推介" not in ln
                ]
                text = "\n".join(lines).strip()
    except Exception:
        text = ""

    text = strip_html(text)
    if not text or len(text) < 80:
        try:
            from readability import Document

            doc = Document(raw)
            summary_html = doc.summary(html_partial=True)
            root = lxml_html.fromstring(summary_html)
            text = root.text_content()
            text = strip_html(text)
        except Exception:
            text = ""

    if text:
        text = clean_content_text(text)
        cache[url] = {"text": text, "image": image_url, "timestamp": now}
        return text, image_url

    return cached_text, cached_image


def extract_full_html(url: str, cache: dict, image_cache: dict | None = None) -> str:
    if not url:
        return ""
    now = time.time()
    entry = cache.get(url, {})
    cached_html = entry.get("html", "")
    cached_ts = float(entry.get("timestamp", 0) or 0)
    if cached_html and (now - cached_ts) <= FULLHTML_CACHE_TTL:
        if "cnbeta.com.tw" in url:
            if "<img" not in cached_html:
                cached_html = ""
            else:
                img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', cached_html)
                if img_srcs and all("cnbeta.com.tw/articles/" in s for s in img_srcs):
                    cached_html = ""
        if cached_html:
            if "cnbeta.com.tw" in url and TRAD_CONVERTER is not None:
                converted = clean_html_fragment(cached_html, url, image_cache)
                if converted and converted != cached_html:
                    cache[url] = {"html": converted, "timestamp": now}
                    return converted
            return cached_html
    try:
        from lxml import html as lxml_html
    except Exception:
        return ""
    fetch_url = safe_fetch_url(url)
    try:
        req = urllib.request.Request(fetch_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return cached_html
    if "on.cc" in url:
        text, _ = extract_oncc_content_and_image(raw)
        if text:
            html_text = "<br>".join(html.escape(text).splitlines())
            cache[url] = {"html": html_text, "timestamp": now}
            return html_text
    try:
        root = lxml_html.fromstring(raw)
        if "news.rthk.hk" in url:
            nodes = root.xpath("//div[contains(@class,'itemFullText')]")
        elif "mingpao.com" in url:
            nodes = root.xpath(
                "//div[@id='blockcontent']//article[contains(@class,'txt4')] | "
                "//article[contains(@class,'txt4')] | "
                "//div[@id='upper'] | "
                "//div[@id='articleContent'] | "
                "//div[contains(@class,'articleContent')] | "
                "//div[contains(@class,'articlecontent')] | "
                "//div[contains(@class,'articleDetail')] | "
                "//div[contains(@class,'article')]"
            )
        elif "stheadline.com" in url:
            nodes = root.xpath(
                "//div[contains(@class,'article-content')] | "
                "//div[contains(@class,'main-body')] | "
                "//div[contains(@class,'content-main')] | "
                "//div[contains(@class,'article-details-content-container')]"
            )
        else:
            nodes = root.xpath("//article")
        best_node = None
        best_len = 0
        for node in nodes:
            text = node.text_content() or ""
            if "stheadline.com" in url:
                cls = node.get("class") or ""
                if "hit-articles" in cls or "related" in cls:
                    continue
                img_count = len(
                    node.xpath(".//img[contains(@src,'image.hkhl.hk') or contains(@data-src,'image.hkhl.hk')]")
                )
                length = len(text) + img_count * 800
            else:
                length = len(text)
            if "ol.mingpao.com" in url:
                time_count = len(re.findall(r"\(\d{1,2}:\d{2}\)", text))
                if time_count >= 3:
                    continue
            if length > best_len:
                best_node = node
                best_len = length
        if best_node is not None and best_len >= 200:
            fragment = lxml_html.tostring(best_node, encoding="unicode")
            fragment = clean_html_fragment(fragment, url, image_cache)
            if fragment:
                if "cnbeta.com.tw" in url and "<img" not in fragment:
                    fragment = ""
                else:
                    cache[url] = {"html": fragment, "timestamp": now}
                    return fragment
        # fallback to readability if selected node is too short or missing
        try:
            from readability import Document

            doc = Document(raw)
            summary_html = doc.summary(html_partial=True)
            fragment = clean_html_fragment(summary_html, url, image_cache)
            if fragment:
                cache[url] = {"html": fragment, "timestamp": now}
            return fragment
        except Exception:
            return ""
    except Exception:
        return cached_html


def parse_items(payload: bytes | str, source: str, category: str = "") -> list[Item]:
    items: list[Item] = []
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="ignore")
    else:
        text = payload
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    try:
        root = ET.fromstring(text)
        for item in root.findall(".//item"):
            title = find_text(item, "title")
            link = find_text(item, "link")
            pub_text = find_text(item, "pubDate")
            pub_dt = parse_pub_date(pub_text)
            summary = find_text(item, "encoded") or find_text(item, "description")
            rss_image = get_rss_image(item, link)
            items.append(
                Item(
                    title=to_trad_if_cnbeta(source, strip_html(title)),
                    link=link,
                    pub_dt=pub_dt,
                    pub_text=pub_text,
                    source=source,
                    category=category,
                    summary=to_trad_if_cnbeta(source, strip_html(summary)),
                    rss_image=rss_image,
                )
            )
        return items
    except ET.ParseError:
        try:
            from lxml import etree as lxml_etree

            parser = lxml_etree.XMLParser(recover=True)
            root = lxml_etree.fromstring(text.encode("utf-8"), parser=parser)

            def lxml_text(node: Any, tag: str) -> str:
                child = node.find(tag)
                if child is None:
                    return ""
                return (child.text or "").strip()

            for item in root.xpath("//item"):
                title = lxml_text(item, "title")
                link = lxml_text(item, "link")
                pub_text = lxml_text(item, "pubDate")
                pub_dt = parse_pub_date(pub_text)
                desc_raw = lxml_text(item, "encoded") or lxml_text(item, "description")
                summary = desc_raw
                rss_image = get_rss_image_from_desc(desc_raw, link)
                items.append(
                    Item(
                        title=to_trad_if_cnbeta(source, strip_html(title)),
                        link=link,
                        pub_dt=pub_dt,
                        pub_text=pub_text,
                        source=source,
                        category=category,
                        summary=to_trad_if_cnbeta(source, strip_html(summary)),
                        rss_image=rss_image,
                    )
                )
            return items
        except Exception:
            raise


def parse_oncc_datetime(text: str) -> datetime | None:
    if not text:
        return None
    m = re.search(r"(\\d{4})年(\\d{2})月(\\d{2})日\\s+(\\d{2}):(\\d{2})", text)
    if not m:
        return None
    year, month, day, hour, minute = map(int, m.groups())
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def parse_oncc_datetime_iso(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
            ZoneInfo("Asia/Hong_Kong")
        )
    except Exception:
        return None


def extract_oncc_content(raw_html: str) -> str:
    m = re.search(r'"content"\s*:\s*"(.*?)"\s*,\s*"', raw_html, re.S)
    if not m:
        return ""
    text = m.group(1)
    text = text.replace("\\n", "\n").replace("\\r", "\n").replace("\\t", " ")
    text = text.replace("\\/", "/")
    text = html.unescape(text)
    text = text.replace("<br/>", "\n").replace("<br />", "\n").replace("<br>", "\n")
    text = strip_html(text)
    text = clean_content_text(text)
    return text.strip()


def extract_oncc_content_and_image(raw_html: str) -> tuple[str, str]:
    image_url = ""
    try:
        from lxml import html as lxml_html
        root = lxml_html.fromstring(raw_html)
        ld = root.xpath('//script[@type="application/ld+json"]/text()')
        if ld:
            try:
                data = json.loads(ld[0])
                image = data.get("image") or ""
                if isinstance(image, list) and image:
                    image_url = image[0]
                elif isinstance(image, str):
                    image_url = image
            except Exception:
                pass
    except Exception:
        image_url = ""
    return extract_oncc_content(raw_html), image_url


def extract_hk01_article(
    raw_html: str,
) -> tuple[str, str, str, datetime | None, list[str]]:
    title = ""
    content = ""
    image_url = ""
    pub_dt = None
    extra_images: list[str] = []
    try:
        from lxml import html as lxml_html
        root = lxml_html.fromstring(raw_html)
        title = root.xpath("string(//h1)") or ""
        title = title.strip()
        ld = root.xpath('//script[@type="application/ld+json"]/text()')
        for blob in ld:
            if "NewsArticle" not in blob:
                continue
            try:
                data = json.loads(blob)
            except Exception:
                continue
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict) and entry.get("@type") == "NewsArticle":
                        data = entry
                        break
            if isinstance(data, dict):
                image = data.get("image") or ""
                if isinstance(image, list) and image:
                    image_url = image[0]
                elif isinstance(image, str):
                    image_url = image
                if not title:
                    title = data.get("headline", "") or title
            break
        m = re.search(r'__NEXT_DATA__\" type=\"application/json\">(.*?)</script>', raw_html, re.S)
        if m:
            try:
                obj = json.loads(m.group(1))
                article = (
                    obj.get("props", {})
                    .get("initialProps", {})
                    .get("pageProps", {})
                    .get("article", {})
                )
                if not title:
                    title = article.get("title", "") or title
                if not image_url:
                    main = article.get("mainImage") or article.get("originalImage") or {}
                    if isinstance(main, dict):
                        image_url = main.get("cdnUrl") or image_url
                thumbs = article.get("thumbnails") or []
                if isinstance(thumbs, list):
                    for thumb in thumbs:
                        if isinstance(thumb, dict):
                            cdn = thumb.get("cdnUrl") or ""
                            if cdn:
                                extra_images.append(cdn)
                original = article.get("originalImage") or {}
                if isinstance(original, dict):
                    cdn = original.get("cdnUrl") or ""
                    if cdn:
                        extra_images.append(cdn)
                ts = article.get("publishTime")
                if isinstance(ts, (int, float)) and ts > 0:
                    pub_dt = datetime.fromtimestamp(ts, ZoneInfo("Asia/Hong_Kong"))
                blocks = article.get("blocks") or []
                parts: list[str] = []
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    for tok in b.get("htmlTokens") or []:
                        if not isinstance(tok, list):
                            continue
                        for t in tok:
                            if isinstance(t, dict) and t.get("content"):
                                parts.append(str(t.get("content")))
                if parts:
                    content = "\n".join(parts)
            except Exception:
                pass
    except Exception:
        pass
    content = clean_content_text(strip_html(content))
    if image_url:
        image_url = normalize_image_url("", image_url)
    if extra_images:
        seen: set[str] = set()
        hero_norm = image_url.split("?")[0] if image_url else ""
        cleaned: list[str] = []
        for img in extra_images:
            img = normalize_image_url("", img)
            norm = img.split("?")[0]
            if not norm or norm == hero_norm:
                continue
            if norm in seen:
                continue
            seen.add(norm)
            cleaned.append(img)
        extra_images = cleaned
    return title, content, image_url, pub_dt, extra_images


def fetch_hk01_list(url: str, feed_cache: dict, category: str = "news", lock: Optional[threading.Lock] = None) -> list[Item]:
    items: list[Item] = []
    payload, meta = fetch_with_cache(url, feed_cache)
    if meta:
        if lock:
            lock.acquire()
        try:
            entry = feed_cache.get(url, {})
            if payload:
                entry["payload_b64"] = base64.b64encode(payload).decode("ascii")
            if meta.get("etag"):
                entry["etag"] = meta.get("etag")
            if meta.get("last_modified"):
                entry["last_modified"] = meta.get("last_modified")
            entry["timestamp"] = meta.get("timestamp", time.time())
            feed_cache[url] = entry
        finally:
            if lock:
                lock.release()
    if not payload:
        return items
    html_text = payload.decode("utf-8", errors="ignore")
    ids: list[str] = []
    m = re.search(r'__NEXT_DATA__\" type=\"application/json\">(.*?)</script>', html_text, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            ids = list(dict.fromkeys(re.findall(r'"articleId"\s*:\s*(\d+)', json.dumps(data))))
        except Exception:
            ids = []
    if not ids:
        ids = list(dict.fromkeys(re.findall(r'"articleId"\s*:\s*(\d+)', html_text)))
    for aid in ids[:HK01_LIMIT]:
        link = f"https://www.hk01.com/article/{aid}"
        try:
            req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        title, content, image_url, pub_dt, extra_images = extract_hk01_article(raw)
        if not content:
            continue
        items.append(
            Item(
                title=title,
                link=link,
                pub_dt=pub_dt,
                pub_text=pub_dt.strftime("%Y-%m-%d %H:%M HKT") if pub_dt else "",
                source="hk01",
                category=category,
                summary=content,
                rss_image=image_url,
                extra_images=extra_images,
            )
        )
    return items

def fetch_oncc_list(url: str, feed_cache: dict, category: str = "news", lock: Optional[threading.Lock] = None) -> list[Item]:
    items: list[Item] = []
    payload, meta = fetch_with_cache(url, feed_cache)
    if meta:
        if lock:
            lock.acquire()
        try:
            entry = feed_cache.get(url, {})
            if payload:
                entry["payload_b64"] = base64.b64encode(payload).decode("ascii")
            if meta.get("etag"):
                entry["etag"] = meta.get("etag")
            if meta.get("last_modified"):
                entry["last_modified"] = meta.get("last_modified")
            entry["timestamp"] = meta.get("timestamp", time.time())
            feed_cache[url] = entry
        finally:
            if lock:
                lock.release()
    if not payload:
        return items
    html_text = payload.decode("utf-8", errors="ignore")
    links = re.findall(
        r'href=\"(/hk/bkn/cnt/(?:news|entertainment)/\d{8}/[^\"]+\.html)\"',
        html_text,
    )
    seen: set[str] = set()
    urls: list[str] = []
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        urls.append(urljoin(url, link))
    for link in urls[:ONCC_LIMIT]:
        try:
            req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        title = ""
        pub_dt = None
        pub_text = ""
        try:
            from lxml import html as lxml_html
            root = lxml_html.fromstring(raw)
            title = root.xpath('string(//h1)') or root.xpath('string(//title)')
            title = (title or "").strip()
            time_text = root.xpath('string(//span[contains(@class,\"date\")] | //span[contains(@class,\"time\")])')
            pub_dt = parse_oncc_datetime(time_text)
            if not pub_dt:
                ld = root.xpath('//script[@type=\"application/ld+json\"]/text()')
                if ld:
                    try:
                        data = json.loads(ld[0])
                        if isinstance(data, dict):
                            pub_dt = parse_oncc_datetime_iso(data.get("datePublished", ""))
                    except Exception:
                        pass
            if pub_dt:
                pub_text = pub_dt.strftime("%Y-%m-%d %H:%M HKT")
        except Exception:
            title = ""
        text, image_url = extract_oncc_content_and_image(raw)
        if not text:
            continue
        items.append(
            Item(
                title=title,
                link=link,
                pub_dt=pub_dt,
                pub_text=pub_text,
                source="oncc",
                category=category,
                summary=text,
                rss_image=image_url,
            )
        )
    return items

def fetch_stheadline_ent_list(url: str, feed_cache: dict, lock: Optional[threading.Lock] = None) -> list[Item]:
    items: list[Item] = []
    payload, meta = fetch_with_cache(url, feed_cache)
    if meta:
        if lock:
            lock.acquire()
        try:
            entry = feed_cache.get(url, {})
            if payload:
                entry["payload_b64"] = base64.b64encode(payload).decode("ascii")
            if meta.get("etag"):
                entry["etag"] = meta.get("etag")
            if meta.get("last_modified"):
                entry["last_modified"] = meta.get("last_modified")
            entry["timestamp"] = meta.get("timestamp", time.time())
            feed_cache[url] = entry
        finally:
            if lock:
                lock.release()
    if not payload:
        return items
    html_text = payload.decode("utf-8", errors="ignore")
    m = re.search(r'token\s*=\s*"([^"]+)"', html_text)
    if not m:
        return items
    token = m.group(1)
    api_url = f"https://www.stheadline.com/loadnextzone/entertainment/?token={token}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
    except Exception:
        return items
    rows = data.get("catslug", {}).get("data", [])
    for row in rows[:SINGTAO_ENT_LIMIT]:
        link = row.get("url") or row.get("redirect_url") or ""
        if link:
            link = urljoin("https://www.stheadline.com", link)
        title = (row.get("title") or "").strip()
        summary = (row.get("digest") or "").strip()
        image_url = ""
        key_image = row.get("key_image") or {}
        if isinstance(key_image, dict):
            image_url = key_image.get("src") or ""
            if not image_url:
                srcset = key_image.get("srcset") or ""
                if srcset:
                    image_url = srcset.split(",")[0].strip().split(" ")[0]
        updated = row.get("updated_at")
        pub_dt = None
        if updated:
            try:
                pub_dt = datetime.fromtimestamp(int(updated), ZoneInfo("Asia/Hong_Kong"))
            except Exception:
                pub_dt = None
        if not title or not link:
            continue
        items.append(
            Item(
                title=title,
                link=link,
                pub_dt=pub_dt,
                pub_text=pub_dt.strftime("%Y-%m-%d %H:%M HKT") if pub_dt else "",
                source="singtao",
                category="ent",
                summary=summary,
                rss_image=image_url,
            )
        )
    return items


def normalize_title(title: str) -> str:
    text = re.sub(r"\s+", "", title or "")
    text = re.sub(r"[\W_]+", "", text)
    return text


def dedupe_items(items: list[Item], threshold: float = 0.92) -> list[Item]:
    deduped: list[Item] = []
    seen: list[str] = []
    seen_set: set[str] = set()
    buckets: dict[str, list[str]] = {}
    for item in items:
        normalized = normalize_title(item.title)
        if not normalized:
            deduped.append(item)
            continue
        if normalized in seen_set:
            continue
        bucket_key = normalized[:8]
        candidates = buckets.get(bucket_key, [])
        duplicate = False
        for prior in candidates:
            if difflib.SequenceMatcher(None, normalized, prior).ratio() >= threshold:
                duplicate = True
                break
        if duplicate:
            continue
        seen.append(normalized)
        seen_set.add(normalized)
        buckets.setdefault(bucket_key, []).append(normalized)
        deduped.append(item)
    return deduped


def filter_recent(items: list[Item], lookback_hours: float) -> list[Item]:
    if lookback_hours <= 0:
        return items
    now = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    cutoff = now - timedelta(hours=lookback_hours)
    filtered = []
    for item in items:
        if item.pub_dt is None:
            filtered.append(item)
        elif item.pub_dt >= cutoff:
            filtered.append(item)
    return filtered


def apply_mixed_mode(items: list[Item], lookback_hours: float) -> list[Item]:
    if not MIXED_MODE:
        return filter_recent(items, lookback_hours)
    grouped: dict[str, list[Item]] = {}
    for item in items:
        grouped.setdefault(item.source, []).append(item)
    result: list[Item] = []
    for source, rows in grouped.items():
        if source == "cnbeta":
            rows.sort(
                key=lambda x: (
                    0 if x.pub_dt is not None else 1,
                    -(x.pub_dt.timestamp()) if x.pub_dt is not None else 0,
                )
            )
            result.extend(rows[:CNBETA_LIMIT])
        elif source == "singtao":
            ent_rows = [r for r in rows if r.category == "ent"]
            news_rows = [r for r in rows if r.category != "ent"]
            result.extend(filter_recent(ent_rows, lookback_hours))
            result.extend(filter_recent(news_rows, 2))
        else:
            result.extend(filter_recent(rows, lookback_hours))
    return result


def extract_keywords(items_texts: list[tuple[str, str]], limit: int = 10) -> list[str]:
    stopwords = {
        "香港",
        "今日",
        "昨天",
        "今天",
        "明天",
        "本港",
        "消息",
        "最新",
        "新聞",
        "現場",
        "報道",
        "表示",
        "指出",
        "認為",
        "相關",
        "詳情",
        "內容",
        "活動",
        "工作",
        "公司",
        "宣布",
        "公布",
        "不過",
        "因此",
        "所以",
        "如果",
        "以及",
        "另外",
        "目前",
        "其中",
        "仍然",
        "已經",
        "正在",
        "將會",
        "可能",
        "希望",
        "需要",
        "可以",
        "沒有",
        "沒有",
        "一個",
        "一起",
        "一起",
        "同時",
        "因為",
        "今日",
        "近日",
        "昨日",
        "今年",
        "明年",
        "去年",
        "方面",
        "情況",
        "方面",
        "持續",
        "這次",
        "這些",
        "這個",
        "那個",
        "大家",
        "記者",
        "消息稱",
        "消息指",
        "消息人士",
        "網民",
        "網上",
        "社交平台",
        "影片",
        "圖片",
        "發布",
        "公布",
        "宣佈",
        "發表",
        "表示",
        "指出",
        "透露",
        "重申",
        "強調",
        "回應",
        "稱",
        "即時",
        "最新",
        "消息",
        "內容",
        "詳情",
        "報道詳情",
        "相關字詞",
        "編輯推介",
        "熱門",
        "活動",
        "計劃",
        "方案",
        "措施",
        "情況",
        "安排",
        "結果",
        "影響",
        "開始",
        "結束",
        "之後",
        "之前",
        "目前",
        "昨日",
        "近日",
        "今早",
        "今晚",
        "今天",
        "今日",
        "上午",
        "下午",
        "晚上",
        "凌晨",
        "本港",
        "此外",
        "另外",
        "同時",
        "因此",
        "其後",
        "其間",
        "同樣",
        "至於",
        "不過",
        "再者",
    }
    surname = set(
        "陳林黃張李王吳劉梁葉蔡鄭曾何許郭謝鄧馮盧彭沈胡潘杜"
        "蕭鍾曹唐傅汪田余姚鄒熊白孟秦邱蘇石方"
    )
    synonym_map = {
        "特區政府": "政府",
        "港府": "政府",
        "政府當局": "政府",
        "警隊": "警察",
        "警方": "警察",
        "立會": "立法會",
        "寒冷天氣警告": "冷天氣警告",
    }
    money_units = {
        "萬元",
        "億元",
        "千元",
        "百萬",
        "十億",
        "百億",
        "平方呎",
        "方呎",
        "平方米",
        "公里",
        "米",
        "公斤",
        "克",
        "度",
        "°c",
        "℃",
        "美金",
        "美元",
        "港元",
        "日圓",
        "人民幣",
        "億美元",
        "億港元",
        "億日圓",
        "億人民幣",
    }
    phrases = {
        "天文台",
        "冷天氣警告",
        "寒冷天氣警告",
        "酷熱天氣警告",
        "黑色暴雨警告",
        "紅色暴雨警告",
        "黃色暴雨警告",
        "八號風球",
        "三號風球",
        "一號風球",
        "強烈季候風信號",
    }
    org_suffixes = {
        "局",
        "署",
        "處",
        "會",
        "院",
        "廳",
        "部",
        "辦",
        "政府",
        "法院",
        "委員會",
        "集團",
        "公司",
        "大學",
        "學校",
        "醫院",
        "銀行",
        "醫管局",
        "天文台",
        "港鐵",
        "機場",
        "法庭",
        "電台",
        "電視台",
    }
    entity_suffixes = {
        "局",
        "署",
        "處",
        "會",
        "院",
        "廳",
        "部",
        "辦",
        "政府",
        "法院",
        "委員會",
        "集團",
        "公司",
        "大學",
        "學校",
        "醫院",
        "銀行",
        "警方",
        "警察",
        "消防",
        "海關",
        "醫管局",
        "天文台",
        "港鐵",
        "機場",
        "警方",
        "法庭",
        "總統",
        "主席",
        "司長",
        "部長",
        "校長",
        "教授",
        "醫生",
        "議員",
        "球會",
        "影業",
        "電台",
        "電視台",
    }
    place_suffixes = {
        "市",
        "區",
        "鎮",
        "縣",
        "省",
        "國",
        "島",
        "灣",
        "海",
        "路",
        "街",
        "道",
        "村",
        "山",
        "河",
        "湖",
        "港",
    }
    weak_chars = set("的了著及與和就於在是有將未可其對於以並及")
    counts: dict[str, float] = {}
    org_regex = re.compile(r"[\u4e00-\u9fff]{2,10}(?:局|署|處|會|院|廳|部|辦|政府|法院|委員會|集團|公司|大學|學校|醫院|銀行|醫管局|天文台|港鐵|機場|法庭|電台|電視台)")
    place_regex = re.compile(r"[\u4e00-\u9fff]{2,10}(?:市|區|鎮|縣|省|國|島|灣|海|路|街|道|村|山|河|湖|港)")

    def normalize(token: str) -> str:
        return synonym_map.get(token, token)

    def is_person(token: str) -> bool:
        return 2 <= len(token) <= 3 and token[0] in surname and token not in stopwords

    def is_org(token: str) -> bool:
        if any(token.endswith(s) for s in org_suffixes):
            return True
        return bool(org_regex.fullmatch(token))

    def is_place(token: str) -> bool:
        if any(token.endswith(s) for s in place_suffixes):
            return True
        return bool(place_regex.fullmatch(token))

    def valid_token(token: str) -> bool:
        if token in stopwords or token in money_units:
            return False
        if re.search(r"(億|萬|千|百|十).*(美元|港元|日圓|人民幣|元)", token):
            return False
        if re.search(r"(美元|港元|日圓|人民幣|元)$", token):
            return False
        if re.search(r"\d", token):
            return False
        if any(ch in weak_chars for ch in token) and len(token) <= 2:
            return False
        return True

    def get_stanza_nlp():
        global STANZA_NLP
        if STANZA_NLP is not None:
            return STANZA_NLP or None
        if stanza is None:
            STANZA_NLP = False
            return None
        try:
            STANZA_NLP = stanza.Pipeline(
                "zh",
                processors="tokenize,ner",
                tokenize_no_ssplit=True,
                use_gpu=False,
                verbose=False,
            )
        except Exception:
            STANZA_NLP = False
        return STANZA_NLP or None

    stanza_nlp = get_stanza_nlp()
    if stanza_nlp:
        for title, body in items_texts:
            text_blob = f"{title}\n{body}".strip()
            if not text_blob:
                continue
            text_blob = text_blob[:4000]
            try:
                doc = stanza_nlp(text_blob)
            except Exception:
                continue
            for ent in getattr(doc, "ents", []):
                if ent.type not in {"PERSON", "ORG", "GPE", "LOC"}:
                    continue
                token = normalize(re.sub(r"\s+", "", ent.text.strip()))
                if len(token) < 2 or not valid_token(token):
                    continue
                score = 2.8 if ent.type in {"PERSON", "ORG"} else 2.4
                if token and token in (title or ""):
                    score += 2.0
                counts[token] = counts.get(token, 0.0) + score
        if counts:
            sorted_tokens = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
            return [t for t, _ in sorted_tokens[:limit]]

    for title, body in items_texts:
        title_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,6}", title or ""))
        body_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,6}", body or ""))
        text_blob = f"{title}\n{body}"
        for ph in phrases:
            if ph in text_blob:
                counts[ph] = counts.get(ph, 0.0) + 4.0
        for m in org_regex.finditer(text_blob):
            token = normalize(m.group(0))
            if valid_token(token):
                counts[token] = counts.get(token, 0.0) + 2.5
        for m in place_regex.finditer(text_blob):
            token = normalize(m.group(0))
            if valid_token(token):
                counts[token] = counts.get(token, 0.0) + 2.0
        for token in title_tokens:
            token = normalize(token)
            if not valid_token(token):
                continue
            score = 3.5
            if is_org(token):
                score += 2.2
            elif is_place(token):
                score += 2.0
            elif is_person(token):
                score += 2.0
            else:
                continue
            counts[token] = counts.get(token, 0.0) + score
        for token in body_tokens:
            token = normalize(token)
            if not valid_token(token):
                continue
            score = 1.2
            if is_org(token):
                score += 1.8
            elif is_place(token):
                score += 1.5
            elif is_person(token):
                score += 1.4
            else:
                continue
            counts[token] = counts.get(token, 0.0) + score
    sorted_tokens = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [t for t, _ in sorted_tokens[:limit]]

def get_category(source: str, category: str = "") -> str:
    if category:
        return category
    if source == "cnbeta":
        return "tech"
    if source in ("mingpao", "RTHK", "singtao", "oncc", "hk01"):
        return "news"
    return "ent"


def build_html(
    items: list[Item],
    output_path: str,
    lookback_hours: float,
    refresh_seconds: int,
    image_cache: dict,
    fulltext_cache: dict,
    fullhtml_cache: dict,
) -> None:
    build_id = str(int(time.time()))
    now_hkt = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%Y-%m-%d %H:%M")
    now_hkt_short = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%m-%d %H:%M")
    latest_pub = ""
    cards = []
    keyword_texts: list[tuple[str, str]] = []
    now_dt = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    prepared: list[dict[str, Any]] = []
    prefetch_tasks: list[tuple[str, str]] = []
    for idx, item in enumerate(items, start=1):
        content = item.summary
        content_html = ""
        image_url = item.rss_image
        if item.link and item.source not in ("oncc",):
            full_html = extract_full_html(item.link, fullhtml_cache, image_cache)
            if full_html:
                text_from_html = strip_html(full_html)
                # accept shorter html if it still has meaningful text or images
                if len(text_from_html) >= 20 or "<img" in full_html:
                    content_html = full_html
                    content = text_from_html
                else:
                    full_html = ""
            if not full_html:
                fulltext, og_image = extract_fulltext_and_image(item.link, fulltext_cache)
                if fulltext:
                    content = fulltext
                if og_image and not is_generic_image(og_image, item.link):
                    if (not image_url) or is_generic_image(image_url, item.link):
                        image_url = og_image
        content = clean_content_text(to_trad_if_cnbeta(item.source, strip_html(content)))
        keyword_texts.append((item.title, content))
        if not content_html:
            content_html = "<br>".join(html.escape(content).splitlines())
        image_count = 0
        extra_images = item.extra_images[:] if item.extra_images else []
        if item.source == "hk01" and extra_images:
            for img in extra_images:
                img = normalize_image_url(item.link, img)
                prefetch_tasks.append((img, item.link))
        if item.source == "singtao" and image_url:
            m = re.search(r"<img[^>]+src=['\"]([^'\"]+)['\"]", content_html)
            if m:
                hero_norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", image_url).split("?")[0]
                body_norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", m.group(1)).split("?")[0]
                if hero_norm == body_norm:
                    image_url = ""
        if item.pub_dt:
            pub_text = item.pub_dt.astimezone(ZoneInfo("Asia/Hong_Kong")).strftime(
                "%m-%d %H:%M HKT"
            )
        else:
            pub_text = item.pub_text or ""
        if pub_text:
            pub_text = pub_text.strip()
            pub_text = re.sub(r"^\\s*\\d{4}[-/\\.]", "", pub_text)
        if idx == 1 and pub_text:
            latest_pub = pub_text.replace(" HKT", "")
        is_recent = False
        if item.pub_dt:
            is_recent = (
                datetime.now(ZoneInfo("Asia/Hong_Kong")) - item.pub_dt
            ).total_seconds() <= 4 * 60 * 60
        date_class = "date recent" if is_recent else "date"
        category = get_category(item.source, item.category)
        if idx == 1:
            age_class = "age-fresh"
        elif item.pub_dt:
            age_hours = (now_dt - item.pub_dt).total_seconds() / 3600
            if age_hours < 4:
                age_class = "age-4"
            elif age_hours < 8:
                age_class = "age-8"
            else:
                age_class = "age-old"
        else:
            age_class = "age-old"
        if image_url:
            image_url = normalize_image_url(item.link, image_url)
            prefetch_tasks.append((image_url, item.link))
        prepared.append(
            {
                "idx": idx,
                "item": item,
                "content": content,
                "content_html": content_html,
                "image_url": image_url,
                "extra_images": extra_images,
                "pub_text": pub_text,
                "date_class": date_class,
                "age_class": age_class,
                "category": category,
                "image_count": image_count,
            }
        )

    if prefetch_tasks:
        dedup: list[tuple[str, str]] = []
        seen: set[str] = set()
        for url, ref in prefetch_tasks:
            if not url:
                continue
            if url in seen:
                continue
            seen.add(url)
            dedup.append((url, ref))
        dl_lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=DEFAULT_THREADS) as ex:
            list(ex.map(lambda t: download_image(t[0], image_cache, t[1], lock=dl_lock), dedup))

    for row in prepared:
        idx = row["idx"]
        item = row["item"]
        content_html = row["content_html"]
        image_url = row["image_url"]
        extra_images = row["extra_images"]
        pub_text = row["pub_text"]
        date_class = row["date_class"]
        age_class = row["age_class"]
        category = row["category"]
        image_count = row["image_count"]
        hero_html = ""
        hero_caption = ""
        hero_attr = ""
        prefetch_src = ""
        if item.source == "hk01" and extra_images:
            extra_html_parts: list[str] = []
            for img in extra_images:
                img = normalize_image_url(item.link, img)
                local_name = download_image(img, image_cache, item.link)
                if local_name:
                    img_url = f"images/{local_name}?v={build_id}"
                else:
                    img_url = f"{img}?v={build_id}"
                extra_html_parts.append(
                    f"<img src='{html.escape(img_url)}' alt='' loading='lazy' decoding='async'>"
                )
            if extra_html_parts:
                content_html = "<br>".join(extra_html_parts) + "<br>" + content_html
                image_count += len(extra_html_parts)
        if image_url:
            local_name = download_image(image_url, image_cache, item.link)
            if local_name:
                hero_url = f"images/{local_name}?v={build_id}"
            else:
                hero_url = f"{image_url}?v={build_id}"
            if item.source != "cnbeta":
                hero_html = f"<img class='hero' src='{html.escape(hero_url)}' alt='' loading='lazy' decoding='async'>"
                hero_attr = f' data-hero-src="{html.escape(hero_url)}"'
                prefetch_src = hero_url
                image_count += 1
        if not prefetch_src and content_html:
            m = re.search(r"<img[^>]+src=['\"]([^'\"]+)['\"]", content_html)
            if m:
                prefetch_src = m.group(1)
                hero_attr = f' data-hero-src="{html.escape(prefetch_src)}"'
        if image_count == 0:
            try:
                from lxml import html as lxml_html
                root = lxml_html.fromstring(f"<div>{content_html}</div>")
                image_count = len(root.xpath(".//img")) + (1 if hero_html else 0)
            except Exception:
                image_count = (1 if hero_html else 0)
        seen_class = ""
        cards.append(
            """
      <article id="item-{idx:02d}" class="card{seen_class} category-{category} {age_class}" data-source="{source}" data-category="{category}" data-title="{title}" data-link="{link}" data-imgcount="{imgcount}"{hero_attr}>
        <header class="card-head">
          <div class="index-col">
            <span class="index">{idx:02d}</span>
            <div class="thumb-spinner"></div>
          </div>
          <div>
            <h2>{title}{seen_label}</h2>
            <div class="meta-row">
              <span class="tag" data-link="{link}">{source}</span>
              <button class="share-btn" aria-label="分享">↗</button>
              <span class="cat cat-{category}">{category_label}</span>
              <span class="{date_class}">{pub}</span>
              {img_count}
            </div>
          </div>
        </header>
        {hero}
        {hero_note}
        <div class="content">{content}</div>
        <button class="collapse-btn" aria-label="收起">▴</button>
      </article>
            """.format(
                idx=idx,
                title=html.escape(item.title),
                source=html.escape(item.source),
                link=html.escape(item.link),
                pub=html.escape(pub_text.replace(" HKT", "")),
                date_class=date_class,
                hero=hero_html,
                hero_note=hero_caption,
                content=content_html,
                seen_class=seen_class,
                category=category,
                category_label=(
                    "新聞"
                    if category == "news"
                    else ("科技" if category == "tech" else ("娛樂" if category == "ent" else "國際"))
                ),
                age_class=age_class,
                img_count=(f"<span class='img-count'>🖼️{image_count}</span>"),
                imgcount=image_count,
                hero_attr=hero_attr,
                seen_label=("<span class='seen-label'>✓ 已讀</span>" if seen_class else ""),
            )
        )

    build_ts = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%Y-%m-%d %H:%M:%S")
    if MIXED_MODE:
        latest_pub_short = re.sub(r"^\\d{4}[-/\\.]", "", latest_pub) if latest_pub else ""
        meta_line = (
            f"更新時間 {now_hkt_short}"
            + (f"｜最新新聞 {latest_pub_short}" if latest_pub_short else "")
        )
    else:
        latest_pub_short = re.sub(r"^\\d{4}[-/\\.]", "", latest_pub) if latest_pub else ""
        meta_line = (
            f"過去{int(lookback_hours)}小時共{len(items)}則｜更新時間 {now_hkt_short}"
            + (f"｜最新新聞 {latest_pub_short}" if latest_pub_short else "")
        )

    news_marquee_items: list[str] = []
    news_marquee_items.append(f"<span class='marquee-count'>總數 {len(items)}</span>")
    marquee_colors = [
        "#ffd166",
        "#9bdeac",
        "#7bdff2",
        "#f4a261",
        "#a0c4ff",
        "#ffadad",
        "#cdb4db",
        "#b8f2e6",
        "#ffb703",
        "#90caf9",
    ]
    def pick_marquee_color() -> str:
        return random.choice(marquee_colors)
    first_link = True
    marquee_source = list(enumerate(items, start=1))
    random.shuffle(marquee_source)
    for idx, item in marquee_source:
        if not item.title:
            continue
        if first_link:
            first_link = False
        else:
            color = pick_marquee_color()
            news_marquee_items.append(f"<span class='marquee-sep' style='color:{color}'>⟡</span>")
        news_marquee_items.append(
            "<a class='marquee-link' href='#item-{idx:02d}'>{title}</a>".format(
                idx=idx, title=html.escape(item.title)
            )
        )
    if len(news_marquee_items) == 1:
        news_marquee_items.append("<span class='marquee-count'>即時焦點</span>")
    marquee_safe = " ".join(news_marquee_items)

    keywords = extract_keywords(keyword_texts, 10)
    kw_marquee_items: list[str] = []
    kw_marquee_items.append(f"<span class='marquee-count'>關鍵字</span>")
    first_kw = True
    for kw in keywords:
        if not kw:
            continue
        if first_kw:
            first_kw = False
        else:
            color = pick_marquee_color()
            kw_marquee_items.append(f"<span class='marquee-sep' style='color:{color}'>⟡</span>")
        kw_marquee_items.append(
            "<a class='kw-link' data-kw='{kw}'>{kw}</a>".format(kw=html.escape(kw))
        )
    kw_marquee_safe = " ".join(kw_marquee_items)

    html_text = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>即時焦點</title>
  <style>
    :root {{
      --bg: #f6f2ea;
      --card: #ffffff;
      --fg: #1d1d1f;
      --muted: #6a6a6a;
      --accent: #0b5fff;
      --recent: #c1121f;
      --border: #e7e2d8;
      --shadow: rgba(10, 10, 10, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans TC", "PingFang TC", "Heiti TC", sans-serif;
      background: var(--bg);
      color: var(--fg);
      line-height: 1.7;
      -webkit-text-size-adjust: 100%;
    }}
    header.site {{
      position: sticky;
      top: 0;
      z-index: 20;
      padding: calc(24px + env(safe-area-inset-top)) 16px 8px;
      text-align: center;
      background: var(--bg);
      box-shadow: 0 6px 18px rgba(0,0,0,0.06);
    }}
    header.site h1 {{
      font-size: 14px;
      margin: 0 0 6px;
      font-weight: 600;
    }}
    .marquee {{
      position: relative;
      overflow: hidden;
      white-space: nowrap;
      font-size: 14px;
      user-select: none;
      touch-action: pan-y;
    }}
    .kw-marquee {{
      width: 100%;
      overflow: hidden;
      white-space: nowrap;
      font-size: 14px;
      color: var(--muted);
    }}
    .marquee-track {{
      display: inline-block;
      animation: marquee 2106s linear infinite;
      will-change: transform;
    }}
    .kw-track {{
      display: inline-block;
      animation: kwmarquee 36s linear infinite;
      will-change: transform;
    }}
    .marquee-item {{
      display: inline-block;
      margin-right: 28px;
    }}
    .marquee-link {{
      color: inherit;
      text-decoration: none;
      border-bottom: 1px solid rgba(255,255,255,0.12);
      padding-bottom: 2px;
    }}
    .marquee-link:hover {{
      opacity: 0.85;
    }}
    .kw-link {{
      color: inherit;
      text-decoration: none;
      border-bottom: 1px dotted rgba(0,0,0,0.2);
      padding-bottom: 1px;
    }}
    .marquee-count {{
      font-weight: 600;
      opacity: 0.9;
    }}
    .marquee-sep {{
      padding: 0 10px;
      font-weight: 600;
    }}
    @keyframes marquee {{
      0% {{ transform: translateX(calc(var(--marquee-offset, 0px) + 0px)); }}
      100% {{ transform: translateX(calc(var(--marquee-offset, 0px) - 100%)); }}
    }}
    @keyframes kwmarquee {{
      0% {{ transform: translateX(0); }}
      100% {{ transform: translateX(-100%); }}
    }}
    @keyframes spin {{
      to {{ transform: rotate(360deg); }}
    }}
    @media (min-width: 900px) {{
      .marquee {{
        font-size: 14px;
      }}
    }}
    header.site .meta {{
      color: var(--muted);
      font-size: 10px;
    }}
    .toolbar {{
      position: static;
      background: transparent;
      backdrop-filter: none;
      border-bottom: none;
      padding: 12px 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      align-items: center;
      justify-content: center;
    }}
    .toolbar-row {{
      width: 100%;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: center;
      align-items: center;
    }}
    .search-wrap {{
      flex: 1 1 260px;
      max-width: 560px;
      display: flex;
      align-items: center;
      position: relative;
    }}
    .toolbar input {{
      width: 100%;
      padding: 10px 36px 10px 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: #fff;
      font-size: 14px;
    }}
    .clear-search {{
      position: absolute;
      right: 10px;
      border: 0;
      background: transparent;
      font-size: 16px;
      color: var(--muted);
      cursor: pointer;
      display: none;
      padding: 0;
      line-height: 1;
    }}
    .clear-search.visible {{
      display: block;
    }}
    .filters {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: center;
    }}
    .filters.secondary {{
      display: none;
      flex-wrap: wrap;
      justify-content: center;
    }}
    .filters.secondary.show {{
      display: flex;
    }}
    .submeta {{
      width: 100%;
      text-align: center;
      color: var(--muted);
      font-size: 12px;
    }}
    .chip {{
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 12px;
      background: #fff;
      cursor: pointer;
      user-select: none;
      touch-action: manipulation;
    }}
    .chip.active {{
      background: var(--accent);
      color: #fff;
      border-color: transparent;
    }}
    .cat {{
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      border: 1px solid var(--border);
      background: #fff;
    }}
    .cat-news {{
      border-color: #c9ddff;
      color: #1c4e9a;
      background: #eef5ff;
    }}
    .cat-tech {{
      border-color: #cfead6;
      color: #1f6b3a;
      background: #eefaf2;
    }}
    .cat-ent {{
      border-color: #f5c6cd;
      color: #8d2b3c;
      background: #fff1f2;
    }}
    .cat-intl {{
      border-color: #cfd7ff;
      color: #2b3f8d;
      background: #eef0ff;
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 16px;
      display: grid;
      gap: 16px;
    }}
    .card {{
      position: relative;
      background: var(--card);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 10px 24px var(--shadow);
      border: 1px solid var(--border);
      scroll-margin-top: 180px;
      opacity: 0;
      transform: translateY(10px);
      transition: opacity 0.5s ease, transform 0.5s ease;
    }}
    .card.show {{
      opacity: 1;
      transform: translateY(0);
    }}
    .index-col {{
      display: inline-flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      min-width: 38px;
    }}
    .thumb-spinner {{
      width: 22px;
      height: 22px;
      border-radius: 50%;
      border: 2px solid rgba(0, 0, 0, 0.12);
      border-top-color: var(--accent);
      animation: spin 0.9s linear infinite;
      opacity: 0;
    }}
    .card.collapsed .thumb-spinner {{
      opacity: 1;
    }}
    .card[data-imgcount="0"] .thumb-spinner {{
      opacity: 0;
    }}
    .card.img-loaded .thumb-spinner {{
      opacity: 0;
    }}
    .card.hi {{
      outline: 3px solid rgba(255, 184, 0, 0.75);
      box-shadow: 0 0 0 6px rgba(255, 184, 0, 0.18);
    }}
    .card.focus {{
      outline: 3px solid rgba(52, 120, 255, 0.65);
      box-shadow: 0 0 0 6px rgba(52, 120, 255, 0.15);
    }}
    html, body {{
      scroll-snap-type: none;
      scroll-padding-top: 12px;
      scroll-behavior: smooth;
    }}
    .card.seen {{
      filter: saturate(0.92);
      opacity: 0.86;
    }}
    .seen-label {{
      display: inline-block;
      margin-left: 6px;
      font-size: 11px;
      color: #5573a6;
      font-weight: 600;
      letter-spacing: 0.5px;
      vertical-align: middle;
    }}
    .category-news {{
      --cat-bg: #eef5ff;
      --cat-bg-2: #e3efff;
      --cat-bg-3: #d7e8ff;
    }}
    .category-tech {{
      --cat-bg: #eefaf2;
      --cat-bg-2: #e1f5e8;
      --cat-bg-3: #d2efdd;
    }}
    .category-ent {{
      --cat-bg: #fff1f2;
      --cat-bg-2: #ffe3e6;
      --cat-bg-3: #ffd4da;
    }}
    .category-intl {{
      --cat-bg: #eef0ff;
      --cat-bg-2: #e4e7ff;
      --cat-bg-3: #d6dbff;
    }}
    .age-fresh {{
      background: #ffffff;
    }}
    .age-4 {{
      background: var(--cat-bg, #ffffff);
    }}
    .age-8 {{
      background: var(--cat-bg-2, #f3f3f3);
    }}
    .age-old {{
      background: var(--cat-bg-3, #eeeeee);
    }}
    .card-head {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 12px;
      align-items: start;
    }}
    .index {{
      background: #f0ede7;
      border-radius: 12px;
      padding: 6px 10px;
      font-weight: 700;
      font-size: 12px;
      color: #8a6d3b;
    }}
    h2 {{
      margin: 0 0 6px;
      font-size: 14px;
      line-height: 1.4;
      font-family: "Noto Serif TC", "PingFang TC", "Heiti TC", serif;
    }}
    .meta-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .tag {{
      padding: 2px 10px;
      border-radius: 999px;
      background: #eef2f7;
      font-size: 11px;
      cursor: pointer;
      touch-action: manipulation;
    }}
    .date {{
      color: var(--accent);
      font-size: 12px;
    }}
    .img-count {{
      color: var(--muted);
      font-size: 12px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }}
    .collapse-btn {{
      position: absolute;
      right: 12px;
      bottom: 8px;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--accent);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      cursor: pointer;
      touch-action: manipulation;
    }}
    .card.collapsed .collapse-btn {{
      display: none;
    }}
    .date.recent {{
      color: var(--recent);
      font-weight: 600;
    }}
    .refresh-btn {{
      border: 1px solid var(--border);
      background: #fff;
      color: var(--accent);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 14px;
      cursor: pointer;
      touch-action: manipulation;
    }}
    .font-btn {{
      border: 1px solid var(--border);
      background: #fff;
      color: var(--accent);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      cursor: pointer;
      touch-action: manipulation;
    }}
    .refresh-btn:hover {{
      border-color: var(--accent);
    }}
    .view-btn {{
      border: 1px solid var(--border);
      background: #fff;
      color: var(--accent);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      cursor: pointer;
      touch-action: manipulation;
    }}
    .hero {{
      width: 100% !important;
      height: auto;
      max-height: 420px;
      object-fit: contain;
      border-radius: 12px;
      margin: 10px auto;
      display: block;
    }}
    .card[data-source="singtao"] .hero {{
      object-fit: contain;
      background: #fff;
    }}
    .content {{
      font-size: var(--content-font, 15px);
      color: #262626;
      white-space: normal;
      position: relative;
      padding-bottom: 26px;
    }}
    .content h1, .content h2, .content h3 {{
      font-size: 16px;
      line-height: 1.5;
      margin: 8px 0;
    }}
    .share-btn {{
      border: 1px solid var(--border);
      background: #fff;
      color: var(--accent);
      border-radius: 999px;
      width: 22px;
      height: 22px;
      line-height: 20px;
      text-align: center;
      font-size: 12px;
      cursor: pointer;
      user-select: none;
      touch-action: manipulation;
    }}
    .card .content {{
      transition: max-height 0.35s ease, opacity 0.35s ease, transform 0.35s ease;
    }}
    .card.collapsed .content {{
      opacity: 0;
      transform: translateY(-6px);
    }}
    .card.collapsed.show .content {{
      opacity: 0;
      transform: translateY(-6px);
    }}
    .card.collapsed .content,
    .card.collapsed .hero,
    .card.collapsed .img-note {{
      display: none;
    }}
    .card.collapsed .img-wrap,
    .card.collapsed .img-spinner {{
      display: none;
    }}
    .content img {{
      display: block;
      margin-left: auto;
      margin-right: auto;
      width: 100% !important;
      max-width: 100% !important;
      height: auto;
      max-height: 420px;
      object-fit: contain;
      border-radius: 12px;
      margin-top: 8px;
      margin-bottom: 8px;
      opacity: 0;
      transform: translateY(8px);
      transition: opacity 0.5s ease, transform 0.5s ease;
    }}
    .img-wrap {{
      position: relative;
      display: block;
    }}
    .img-spinner {{
      position: absolute;
      top: 50%;
      left: 50%;
      width: 26px;
      height: 26px;
      margin: -13px 0 0 -13px;
      border-radius: 50%;
      border: 3px solid rgba(0, 0, 0, 0.12);
      border-top-color: var(--accent);
      animation: spin 0.9s linear infinite;
      pointer-events: none;
    }}
    .img-spinner.hide {{
      opacity: 0;
      transition: opacity 0.2s ease;
    }}
    .content img.show {{
      opacity: 1;
      transform: translateY(0);
    }}
    .card[data-source="singtao"] .content img {{
      max-height: 320px;
      background: #fff;
    }}
    .hl {{
      background: #fff3a6;
      padding: 0 2px;
      border-radius: 2px;
    }}
    .content a {{
      color: var(--accent);
    }}
    .content table {{
      width: 100%;
      border-collapse: collapse;
      margin: 8px 0;
      font-size: 14px;
    }}
    .content th, .content td {{
      border: 1px solid #e0e0e0;
      padding: 6px;
      text-align: left;
    }}
    .img-note {{
      font-size: 12px;
      color: var(--muted);
      margin: -2px 0 8px;
      word-break: break-all;
    }}
    .empty {{
      text-align: center;
      color: var(--muted);
      padding: 40px 16px;
    }}
    .site-footer {{
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      padding: 18px 16px 28px;
    }}
    .top-btn {{
      position: fixed;
      right: max(12px, calc(50% - 460px + 8px));
      bottom: 16px;
      z-index: 20;
      border: 1px solid var(--border);
      background: #fff;
      color: var(--accent);
      border-radius: 999px;
      padding: 8px 10px;
      font-size: 12px;
      cursor: pointer;
      display: none;
      touch-action: manipulation;
    }}
    .top-btn.show {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }}
    .modal {{
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 50;
      padding: 16px;
    }}
    .modal.active {{
      display: flex;
    }}
    .modal-card {{
      background: #fff;
      border-radius: 16px;
      width: min(1100px, 100%);
      height: min(80vh, 900px);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border: 1px solid var(--border);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.2);
    }}
    .modal-head {{
      padding: 10px 14px;
      display: flex;
      align-items: center;
      gap: 10px;
      border-bottom: 1px solid var(--border);
    }}
    .modal-head .title {{
      font-size: 14px;
      color: var(--muted);
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .modal-head button {{
      border: 0;
      background: #111;
      color: #fff;
      padding: 6px 12px;
      border-radius: 999px;
      cursor: pointer;
      font-size: 12px;
    }}
    .modal-body {{
      flex: 1;
      background: #f7f7f7;
    }}
    .modal-body iframe {{
      width: 100%;
      height: 100%;
      border: 0;
      background: #fff;
    }}
    .open-link {{
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      background: #fff;
      cursor: pointer;
    }}
    @media (max-width: 640px) {{
      header.site h1 {{
        font-size: 22px;
      }}
      .card {{
        padding: 14px;
      }}
      h2 {{
        font-size: 14px;
      }}
      .toolbar {{
        padding: 10px 12px;
      }}
      .search-wrap {{
        flex: 1 1 100%;
        max-width: none;
      }}
      .toolbar input {{
        font-size: 16px;
      }}
    }}
  </style>
</head>
<body>
  <header class="site">
    <h1 class="marquee"><span class="marquee-track">{marquee_safe}</span></h1>
    <div class="kw-marquee"><span class="kw-track">{kw_marquee_safe}</span></div>
    <div class="meta">{meta_line}｜<button class="refresh-btn" id="refresh" title="更新">⟳</button> <button class="font-btn" id="font-sm" title="內文縮細">A-</button> <button class="font-btn" id="font-lg" title="內文放大">A+</button></div>
  </header>
  <div class="toolbar">
      <div class="toolbar-row">
        <div class="search-wrap">
          <input id="search" type="search" placeholder="搜尋標題或內容…">
          <button class="clear-search" id="clear-search" title="清除">✕</button>
        </div>
      </div>
    <div class="toolbar-row">
      <div class="filters">
        <span class="chip active" data-category="all">全部(0)</span>
        <span class="chip" data-category="news">新聞(0)</span>
        <span class="chip" data-category="intl">國際(0)</span>
        <span class="chip" data-category="ent">娛樂(0)</span>
        <span class="chip" data-category="tech">科技(0)</span>
      </div>
    </div>
    <div class="toolbar-row">
      <div class="filters secondary" id="news-sources">
        <span class="chip active" data-source="all">全部</span>
        <span class="chip" data-source="RTHK">RTHK</span>
        <span class="chip" data-source="mingpao">Mingpao</span>
        <span class="chip" data-source="oncc">ON.cc</span>
        <span class="chip" data-source="singtao">Singtao</span>
        <span class="chip" data-source="hk01">HK01</span>
      </div>
    </div>
  </div>
  <main id="list">
    {"".join(cards) if cards else '<div class="empty">近 12 小時內冇新項目。</div>'}
  </main>
  <footer class="site-footer">生成時間 {build_ts} HKT</footer>
  <button class="top-btn" id="top-btn" title="回到頂部">↑ 頂部</button>
  <script>
    const categoryChips = document.querySelectorAll('.filters:not(.secondary) .chip');
    const sourceChips = document.querySelectorAll('.filters.secondary .chip');
    const cards = document.querySelectorAll('.card');
    const search = document.getElementById('search');
    const clearBtn = document.getElementById('clear-search');
    const refreshBtn = document.getElementById('refresh');
    const fontSm = document.getElementById('font-sm');
    const fontLg = document.getElementById('font-lg');
    const topBtn = document.getElementById('top-btn');
    const headerEl = document.querySelector('header.site');
    const toolbarEl = document.querySelector('.toolbar');
    const marquee = document.querySelector('.marquee');
    const marqueeTrack = document.querySelector('.marquee-track');
    const refreshMs = {max(60, int(refresh_seconds))} * 1000;
    let lastAuto = Date.now();
    const contents = Array.from(document.querySelectorAll('.content'));
    const titles = Array.from(document.querySelectorAll('.card h2'));
    const seenSet = new Set(JSON.parse(localStorage.getItem('seenLinks') || '[]'));
    cards.forEach(card => {{
      const link = card.dataset.link || '';
      if (link && seenSet.has(link)) {{
        card.classList.add('seen');
        if (!card.querySelector('.seen-label')) {{
          const label = document.createElement('span');
          label.className = 'seen-label';
          label.textContent = '✓ 已讀';
          const h2 = card.querySelector('h2');
          if (h2) h2.appendChild(label);
        }}
      }}
    }});
    function markSeen(card) {{
      if (!card) return;
      const link = card.dataset.link || '';
      if (!link) return;
      if (!card.classList.contains('seen')) {{
        card.classList.add('seen');
        if (!card.querySelector('.seen-label')) {{
          const label = document.createElement('span');
          label.className = 'seen-label';
          label.textContent = '✓ 已讀';
          const h2 = card.querySelector('h2');
          if (h2) h2.appendChild(label);
        }}
      }}
      seenSet.add(link);
      localStorage.setItem('seenLinks', JSON.stringify(Array.from(seenSet)));
    }}
    // update category counts in chips
    const counts = {{ all: 0, news: 0, intl: 0, ent: 0, tech: 0 }};
    cards.forEach(card => {{
      const cat = card.dataset.category || '';
      counts.all += 1;
      if (counts[cat] !== undefined) counts[cat] += 1;
    }});
    categoryChips.forEach(chip => {{
      const cat = chip.dataset.category || 'all';
      const label = chip.textContent.replace(/\\(\\d+\\)$/,'').trim();
      const n = counts[cat] || 0;
      chip.textContent = `${{label}}(${{n}})`;
    }});
    sourceChips.forEach(chip => {{
      if (!chip.dataset.label) {{
        chip.dataset.label = chip.textContent.replace(/\\(\\d+\\)$/,'').trim();
      }}
    }});
    function updateSourceCounts() {{
      const srcCounts = {{}};
      sourceChips.forEach(chip => {{
        const src = chip.dataset.source || '';
        if (src) srcCounts[src] = 0;
      }});
      if (activeCategory !== 'all') {{
        cards.forEach(card => {{
          if (card.style.display === 'none') return;
          if ((card.dataset.category || '') !== activeCategory) return;
          const src = card.dataset.source || '';
          if (src in srcCounts) srcCounts[src] += 1;
        }});
      }}
      let total = 0;
      Object.keys(srcCounts).forEach(k => {{
        if (k !== 'all') total += srcCounts[k];
      }});
      sourceChips.forEach(chip => {{
        const src = chip.dataset.source || '';
        const base = chip.dataset.label || chip.textContent.replace(/\\(\\d+\\)$/,'').trim();
        if (src === 'all') {{
          chip.textContent = `${{base}}(${{total}})`;
        }} else {{
          chip.textContent = `${{base}}(${{srcCounts[src] || 0}})`;
        }}
      }});
    }}
    contents.forEach(el => {{
      if (!el.dataset.original) el.dataset.original = el.innerHTML;
    }});
    titles.forEach(el => {{
      if (!el.dataset.original) el.dataset.original = el.innerHTML;
    }});
    // animate cards on load
    cards.forEach((card, i) => {{
      setTimeout(() => card.classList.add('show'), 20 + i * 20);
    }});
    // wrap images with spinner while loading (only when expanded)
    function attachSpinner(img) {{
      if (img.closest('.img-wrap')) return;
      const wrap = document.createElement('div');
      wrap.className = 'img-wrap';
      const spinner = document.createElement('div');
      spinner.className = 'img-spinner';
      img.parentNode.insertBefore(wrap, img);
      wrap.appendChild(img);
      wrap.appendChild(spinner);
      const done = () => {{
        img.classList.add('show');
        spinner.classList.add('hide');
        setTimeout(() => spinner.remove(), 250);
        const card = img.closest('.card');
        if (card) card.classList.add('img-loaded');
      }};
      if (img.complete) {{
        done();
      }} else {{
        img.addEventListener('load', done, {{ once: true }});
        img.addEventListener('error', done, {{ once: true }});
      }}
    }}
    function prefetchHero(card) {{
      if (!card || card.dataset.heroPrefetch === '1') return;
      const src = card.dataset.heroSrc || '';
      if (!src) return;
      card.dataset.heroPrefetch = '1';
      const img = new Image();
      img.src = src;
      const done = () => {{
        card.classList.add('img-loaded');
      }};
      img.addEventListener('load', done, {{ once: true }});
      img.addEventListener('error', done, {{ once: true }});
    }}
    function ensureImageSpinners(card) {{
      if (!card) return;
      if (card.classList.contains('collapsed')) return;
      card.querySelectorAll('.content img, .hero').forEach(img => attachSpinner(img));
    }}
    function cleanupSpinners(card) {{
      if (!card) return;
      card.querySelectorAll('.img-spinner').forEach(sp => sp.remove());
    }}
    cards.forEach(card => ensureImageSpinners(card));
    const newsSources = document.getElementById('news-sources');
    let activeCategory = 'all';
    let activeSource = 'all';
    let fontSize = parseInt(localStorage.getItem('contentFont') || '15', 10);
    if (!Number.isFinite(fontSize) || fontSize < 12 || fontSize > 22) {{
      fontSize = 15;
    }}
    document.documentElement.style.setProperty('--content-font', fontSize + 'px');
    let titleOnly = false;


    function applyCollapseByCategory() {{
      cards.forEach(card => {{
        card.classList.add('collapsed');
      }});
    }}

    function applyFilter() {{
      const q = (search.value || '').trim().toLowerCase();
      cards.forEach(card => {{
        const source = card.dataset.source;
        const category = card.dataset.category;
        const text = (card.dataset.title + ' ' + card.textContent).toLowerCase();
        const categoryOk = activeCategory === 'all' || category === activeCategory;
        const sourceOk = activeSource === 'all' || source === activeSource;
        const textOk = !q || text.includes(q);
        card.style.display = categoryOk && sourceOk && textOk ? '' : 'none';
      }});
    }}
    function switchToAll() {{
      activeCategory = 'all';
      activeSource = 'all';
      categoryChips.forEach(c => c.classList.remove('active'));
      if (categoryChips[0]) categoryChips[0].classList.add('active');
      sourceChips.forEach(c => c.classList.remove('active'));
      if (sourceChips[0]) sourceChips[0].classList.add('active');
      newsSources.classList.remove('show');
      applyFilter();
      applyCollapseByCategory();
      updateSourceCounts();
    }}

    categoryChips.forEach(chip => {{
      chip.addEventListener('click', () => {{
        categoryChips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeCategory = chip.dataset.category || 'all';
        if (activeCategory === 'news' || activeCategory === 'ent' || activeCategory === 'intl') {{
          newsSources.classList.add('show');
          activeSource = 'all';
          sourceChips.forEach(c => c.classList.remove('active'));
          sourceChips[0].classList.add('active');
          updateSourceCounts();
        }} else {{
          newsSources.classList.remove('show');
          activeSource = 'all';
          sourceChips.forEach(c => c.classList.remove('active'));
          sourceChips[0].classList.add('active');
        }}
        applyFilter();
        applyCollapseByCategory();
      }});
    }});

    sourceChips.forEach(chip => {{
      chip.addEventListener('click', () => {{
        sourceChips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeSource = chip.dataset.source || 'all';
        applyFilter();
      }});
    }});
    if (refreshBtn) {{
      refreshBtn.addEventListener('click', () => {{
        const base = location.origin + location.pathname + location.search;
        location.replace(base);
      }});
    }}
    if (fontSm) {{
      fontSm.addEventListener('click', () => {{
        fontSize = Math.max(12, fontSize - 1);
        document.documentElement.style.setProperty('--content-font', fontSize + 'px');
        localStorage.setItem('contentFont', fontSize);
      }});
    }}
    if (fontLg) {{
      fontLg.addEventListener('click', () => {{
        fontSize = Math.min(22, fontSize + 1);
        document.documentElement.style.setProperty('--content-font', fontSize + 'px');
        localStorage.setItem('contentFont', fontSize);
      }});
    }}
    function clearHighlights() {{
      contents.forEach(el => {{
        if (el.dataset.original) el.innerHTML = el.dataset.original;
      }});
      titles.forEach(el => {{
        if (el.dataset.original) el.innerHTML = el.dataset.original;
      }});
    }}
    function highlightTerm(term) {{
      if (!term) return;
      clearHighlights();
      const esc = term.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
      const re = new RegExp(esc, 'gi');
      contents.forEach(el => {{
        const card = el.closest('.card');
        if (card && card.style.display === 'none') return;
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach(node => {{
          const txt = node.nodeValue;
          if (!txt || !re.test(txt)) return;
          re.lastIndex = 0;
          const frag = document.createDocumentFragment();
          let last = 0;
          let m;
          while ((m = re.exec(txt)) !== null) {{
            const pre = txt.slice(last, m.index);
            if (pre) frag.appendChild(document.createTextNode(pre));
            const span = document.createElement('span');
            span.className = 'hl';
            span.textContent = m[0];
            frag.appendChild(span);
            last = m.index + m[0].length;
          }}
          const post = txt.slice(last);
          if (post) frag.appendChild(document.createTextNode(post));
          node.parentNode.replaceChild(frag, node);
        }});
      }});
      titles.forEach(el => {{
        const card = el.closest('.card');
        if (card && card.style.display === 'none') return;
        const txt = el.textContent || '';
        if (!re.test(txt)) return;
        re.lastIndex = 0;
        const frag = document.createDocumentFragment();
        let last = 0;
        let m;
        while ((m = re.exec(txt)) !== null) {{
          const pre = txt.slice(last, m.index);
          if (pre) frag.appendChild(document.createTextNode(pre));
          const span = document.createElement('span');
          span.className = 'hl';
          span.textContent = m[0];
          frag.appendChild(span);
          last = m.index + m[0].length;
        }}
        const post = txt.slice(last);
        if (post) frag.appendChild(document.createTextNode(post));
        el.innerHTML = '';
        el.appendChild(frag);
      }});
    }}
    function setClearVisible() {{
      if (!clearBtn) return;
      clearBtn.classList.toggle('visible', !!(search.value || '').trim());
    }}
    setInterval(() => {{
      if (window.scrollY <= 5 && (Date.now() - lastAuto) >= refreshMs) {{
        window.location.reload();
      }}
    }}, 1000);

    search.addEventListener('input', () => {{
      applyFilter();
      const term = (search.value || '').trim();
      if (term) {{
        highlightTerm(term);
      }} else {{
        clearHighlights();
      }}
      setClearVisible();
    }});
    const scrollGap = 0;
    function updateScrollPadding() {{
      const headerH = headerEl ? headerEl.getBoundingClientRect().height : 0;
      const pad = Math.max(12, headerH + scrollGap);
      document.documentElement.style.scrollPaddingTop = pad + 'px';
      document.body.style.scrollPaddingTop = pad + 'px';
    }}
    function temporarilyDisableSnap() {{
      return;
    }}
    function scrollToCard(card) {{
      if (!card) return;
      updateScrollPadding();
      temporarilyDisableSnap();
      const headerH = headerEl ? headerEl.getBoundingClientRect().height : 0;
      const top = card.offsetTop - headerH - scrollGap - 8;
      window.scrollTo({{ top: Math.max(0, top), behavior: 'smooth' }});
    }}
    document.querySelectorAll('.marquee-link').forEach(link => {{
      link.addEventListener('click', (e) => {{
        const href = link.getAttribute('href') || '';
        const id = href.startsWith('#') ? href.slice(1) : '';
        if (!id) return;
        e.preventDefault();
        search.value = '';
        clearHighlights();
        setClearVisible();
        switchToAll();
        const target = document.getElementById(id);
        if (target) {{
          target.classList.remove('collapsed');
          document.querySelectorAll('.card.hi').forEach(c => c.classList.remove('hi'));
          target.classList.add('hi');
          markSeen(target);
          ensureImageSpinners(target);
          scrollToCard(target);
        }}
      }});
    }});
    document.querySelectorAll('.kw-link').forEach(link => {{
      link.addEventListener('click', (e) => {{
        const kw = link.dataset.kw || '';
        if (!kw) return;
        e.preventDefault();
        search.value = kw;
        applyFilter();
        highlightTerm(kw);
        setClearVisible();
        const first = Array.from(cards).find(c => c.style.display !== 'none');
        if (first) {{
          first.classList.remove('collapsed');
          document.querySelectorAll('.card.hi').forEach(c => c.classList.remove('hi'));
          first.classList.add('hi');
          markSeen(first);
          ensureImageSpinners(first);
          scrollToCard(first);
        }}
      }});
    }});
    window.addEventListener('resize', updateScrollPadding);
    updateScrollPadding();

    if (marquee && marqueeTrack) {{
      marquee.classList.remove('dragging');
      marqueeTrack.style.removeProperty('--marquee-offset');
    }}
    if (clearBtn) {{
      clearBtn.addEventListener('click', () => {{
        search.value = '';
        applyFilter();
        clearHighlights();
        setClearVisible();
        search.focus();
      }});
    }}
    setClearVisible();
    applyCollapseByCategory();
    let snapArmed = false;
    let snapIgnoreUntil = 0;
    let focusPauseUntil = 0;
    let focusLockCard = null;
    let focusLockUntil = 0;
    let focusLockScrollY = 0;
    function armSnapOnce() {{
      snapArmed = false;
      snapIgnoreUntil = 0;
      focusPauseUntil = Date.now() + 200;
      return;
    }}
    function releaseSnapOnce() {{
      snapArmed = false;
      return;
    }}

    cards.forEach(card => {{
      card.addEventListener('click', (e) => {{
        if (!card.classList.contains('collapsed')) return;
        if (e.target.closest('a, button, input, .tag, .share-btn, .collapse-btn')) return;
        const beforeTop = card.getBoundingClientRect().top;
        cards.forEach(other => {{
          if (other !== card) {{
            other.classList.add('collapsed');
            cleanupSpinners(other);
          }}
        }});
        document.querySelectorAll('.card.focus').forEach(c => c.classList.remove('focus'));
        card.classList.add('focus');
        card.classList.remove('collapsed');
        markSeen(card);
        ensureImageSpinners(card);
        focusPauseUntil = Date.now() + 2000;
        focusLockCard = card;
        focusLockUntil = Date.now() + 2500;
        focusLockScrollY = window.scrollY;
        armSnapOnce();
        requestAnimationFrame(() => {{
          scrollToCard(card);
        }});
      }});
    }});
    document.querySelectorAll('.collapse-btn').forEach(btn => {{
      btn.addEventListener('click', (e) => {{
        const card = btn.closest('.card');
        if (!card) return;
        card.classList.add('collapsed');
        cleanupSpinners(card);
        requestAnimationFrame(() => {{
          scrollToCard(card);
        }});
        e.stopPropagation();
      }});
    }});
    cards.forEach((card, i) => {{
      if (i < 12) prefetchHero(card);
    }});
    let lastFocus = null;
    function updateFocusByScroll() {{
      const now = Date.now();
      if (now < focusPauseUntil) return;
      if (focusLockCard && now < focusLockUntil) {{
        const dy = Math.abs(window.scrollY - focusLockScrollY);
        if (dy < 120) {{
          document.querySelectorAll('.card.focus').forEach(c => c.classList.remove('focus'));
          focusLockCard.classList.add('focus');
          return;
        }}
      }}
      const headerH = headerEl ? headerEl.getBoundingClientRect().height : 0;
      if (window.scrollY < (headerH - 6)) return;
      const anchorY = headerH + 8;
      let best = null;
      let bestDist = Infinity;
      cards.forEach(card => {{
        if (card.style.display === 'none') return;
        const rect = card.getBoundingClientRect();
        if (rect.bottom <= anchorY) return;
        const dist = Math.abs(rect.top - anchorY);
        if (dist < bestDist) {{
          bestDist = dist;
          best = card;
        }}
      }});
      if (best) {{
        lastFocus = best;
        document.querySelectorAll('.card.focus').forEach(c => c.classList.remove('focus'));
        best.classList.add('focus');
      }}
    }}
    // focus only updates on click; no scroll-based focus updates
    window.addEventListener('scroll', () => {{
      if (!snapArmed) return;
      if (Date.now() < snapIgnoreUntil) return;
      releaseSnapOnce();
    }});
    window.addEventListener('resize', updateFocusByScroll);
    setTimeout(updateFocusByScroll, 50);
    document.querySelectorAll('.share-btn').forEach(btn => {{
      btn.addEventListener('click', async (e) => {{
        const card = btn.closest('.card');
        if (!card) return;
        const link = card.querySelector('.tag')?.dataset?.link || '';
        const title = card.querySelector('h2')?.textContent || '';
        if (!link) return;
        try {{
          if (navigator.share) {{
            await navigator.share({{ title, url: link }});
          }} else if (navigator.clipboard) {{
            await navigator.clipboard.writeText(link);
            btn.textContent = '✓';
            setTimeout(() => (btn.textContent = '↗'), 1000);
          }} else {{
            window.open(link, '_blank');
          }}
        }} catch (err) {{
          window.open(link, '_blank');
        }}
        e.stopPropagation();
      }});
    }});
    if (topBtn) {{
      topBtn.addEventListener('click', () => {{
        document.querySelectorAll('.card.focus').forEach(c => c.classList.remove('focus'));
        temporarilyDisableSnap();
        window.scrollTo({{ top: 0, behavior: 'smooth' }});
      }});
      window.addEventListener('scroll', () => {{
        topBtn.classList.toggle('show', window.scrollY > 600);
      }});
    }}

    document.querySelectorAll('.tag').forEach(tag => {{
      tag.addEventListener('click', () => {{
        const link = tag.dataset.link;
        if (link) window.open(link, '_blank');
      }});
    }});
  </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(html_text)


def fetch_all(urls: list[str], feed_cache: dict) -> list[Item]:
    items: list[Item] = []
    lock = threading.Lock()

    def worker(url: str) -> list[Item]:
        if "stheadline.com/entertainment" in url:
            return fetch_stheadline_ent_list(url, feed_cache, lock=lock)
        if "on.cc" in url:
            if "/intnews/" in url:
                category = "intl"
            elif "entertainment" in url:
                category = "ent"
            else:
                category = "news"
            return fetch_oncc_list(url, feed_cache, category=category, lock=lock)
        if "hk01.com" in url:
            if "/channel/19/" in url or "/zone/5/" in url:
                category = "intl"
            elif "/zone/2/" in url or "/channel/22/" in url:
                category = "ent"
            else:
                category = "news"
            return fetch_hk01_list(url, feed_cache, category=category, lock=lock)

        payload, meta = fetch_with_cache(url, feed_cache)
        if meta:
            with lock:
                entry = feed_cache.get(url, {})
                if payload:
                    entry["payload_b64"] = base64.b64encode(payload).decode("ascii")
                if meta.get("etag"):
                    entry["etag"] = meta.get("etag")
                if meta.get("last_modified"):
                    entry["last_modified"] = meta.get("last_modified")
                entry["timestamp"] = meta.get("timestamp", time.time())
                feed_cache[url] = entry
        if "rthk" in url:
            source = "RTHK"
            category = "news"
        elif "cnbeta" in url:
            source = "cnbeta"
            category = "tech"
        elif "stheadline.com" in url:
            source = "singtao"
            if "realtime-china" in url or "realtime-world" in url:
                category = "intl"
            else:
                category = "news"
        elif "hk01.com" in url:
            source = "hk01"
            category = "news"
        elif "s00007.xml" in url:
            source = "mingpao"
            category = "ent"
        elif "s00004.xml" in url or "s00005.xml" in url:
            source = "mingpao"
            category = "intl"
        else:
            source = "mingpao"
            category = "news"
        try:
            return parse_items(payload, source, category)
        except Exception as exc:
            print(f"Parse failed ({url}): {exc}")
            return []

    with ThreadPoolExecutor(max_workers=DEFAULT_THREADS) as ex:
        futures = [ex.submit(worker, url) for url in urls]
        for fut in as_completed(futures):
            try:
                items.extend(fut.result())
            except Exception as exc:
                print(f"Fetch failed: {exc}")
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URLS)
    parser.add_argument("--lookback-hours", type=float, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--refresh-seconds", type=int, default=DEFAULT_REFRESH_SECONDS)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--output", default=os.path.join(SITE_DIR, "index.html"))
    args = parser.parse_args()

    ensure_dirs()
    feed_cache = load_json(FEED_CACHE_PATH)
    image_cache = load_json(IMAGE_CACHE_PATH)
    fulltext_cache = load_json(FULLTEXT_CACHE_PATH)
    fullhtml_cache = load_json(FULLHTML_CACHE_PATH)
    feed_cache = gc_cache(feed_cache, CACHE_GC_TTL)
    image_cache = gc_cache(image_cache, CACHE_GC_TTL)
    fulltext_cache = gc_cache(fulltext_cache, CACHE_GC_TTL)
    fullhtml_cache = gc_cache(fullhtml_cache, CACHE_GC_TTL)

    urls = [u.strip() for u in args.url.split(",") if u.strip()]
    items = fetch_all(urls, feed_cache)

    items = apply_mixed_mode(items, args.lookback_hours)
    items = dedupe_items(items)
    items.sort(
        key=lambda x: (
            0 if x.pub_dt is not None else 1,
            -(x.pub_dt.timestamp()) if x.pub_dt is not None else 0,
        )
    )
    if args.max_items > 0:
        items = items[: args.max_items]

    build_html(
        items,
        args.output,
        args.lookback_hours,
        args.refresh_seconds,
        image_cache,
        fulltext_cache,
        fullhtml_cache,
    )

    save_json(FEED_CACHE_PATH, feed_cache)
    save_json(FULLTEXT_CACHE_PATH, fulltext_cache)
    save_json(IMAGE_CACHE_PATH, image_cache)
    save_json(FULLHTML_CACHE_PATH, fullhtml_cache)
    # seen state handled in browser via localStorage
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
