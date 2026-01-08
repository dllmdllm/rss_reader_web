#!/usr/bin/env python3
import argparse
import base64
import hashlib
import difflib
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit, quote

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo
try:
    from opencc import OpenCC
except Exception:
    OpenCC = None


DEFAULT_URLS = ",".join(
    [
        "https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_clocal.xml",
        "https://news.mingpao.com/rss/ins/all.xml",
        "https://news.mingpao.com/rss/ins/s00004.xml",
        "https://news.mingpao.com/rss/ins/s00005.xml",
        "https://news.mingpao.com/rss/ins/s00007.xml",
        "https://rss.cnbeta.com.tw/",
        "https://hk.on.cc/hk/news/index.html",
        "https://www.stheadline.com/rss",
    ]
)
DEFAULT_LOOKBACK_HOURS = 6
DEFAULT_REFRESH_SECONDS = 600
DEFAULT_MAX_ITEMS = 200
DEFAULT_THREADS = 4
CNBETA_LIMIT = 50
MIXED_MODE = True
ONCC_LIMIT = 20

PROJECT_ROOT = os.path.dirname(__file__)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SITE_DIR = PROJECT_ROOT
IMAGES_DIR = os.path.join(SITE_DIR, "images")
FEED_CACHE_PATH = os.path.join(DATA_DIR, "feed_cache.json")
FULLTEXT_CACHE_PATH = os.path.join(DATA_DIR, "fulltext_cache.json")
IMAGE_CACHE_PATH = os.path.join(DATA_DIR, "image_cache.json")
FULLHTML_CACHE_PATH = os.path.join(DATA_DIR, "fullhtml_cache.json")
SEEN_CACHE_PATH = os.path.join(DATA_DIR, "seen_cache.json")

FULLTEXT_CACHE_TTL = 6 * 60 * 60
IMAGE_CACHE_TTL = 24 * 60 * 60
FULLHTML_CACHE_TTL = 6 * 60 * 60
CACHE_GC_TTL = 7 * 24 * 60 * 60
SEEN_CACHE_TTL = 30 * 24 * 60 * 60


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


def mark_seen(seen_cache: dict, items: list["Item"]) -> dict:
    now = time.time()
    for item in items:
        if item.link:
            seen_cache[item.link] = {"timestamp": now}
    return seen_cache


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
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


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
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                payload = resp.read()
                meta = {
                    "etag": resp.headers.get("ETag") or "",
                    "last_modified": resp.headers.get("Last-Modified") or "",
                    "timestamp": time.time(),
                }
                return payload, meta
        with urllib.request.urlopen(req, timeout=20) as resp:
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
        raise


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
            or "相關新聞" in line
            or "立即下載星島頭條App" in line
            or "星島頭條App" in line
            or "即睇減息部署" in line
            or "同場加映" in line
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
        ):
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
            for node in root.xpath(".//*[contains(text(),'同場加映') or contains(text(),'星島頭條App') or contains(text(),'即睇減息部署')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'相關新聞')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
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
        if image_cache is not None:
            for img in root.xpath(".//img[@src]"):
                src = img.get("src")
                if not src:
                    continue
                local_name = download_image(src, image_cache, base_url)
                if local_name:
                    img.set("src", f"images/{local_name}")
        if "cnbeta.com.tw" in base_url:
            imgs = root.xpath(".//img")
            if len(imgs) > 1:
                imgs[0].drop_tag()
        if "stheadline.com" in base_url:
            imgs = list(root.xpath(".//img"))
            seen_src: set[str] = set()
            if imgs:
                first_src = imgs[0].get("src") or ""
                if first_src:
                    norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", first_src)
                    norm = norm.split("?")[0]
                    seen_src.add(norm)
                imgs[0].drop_tag()
            for img in imgs[1:]:
                src = img.get("src") or ""
                if src:
                    norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", src)
                    norm = norm.split("?")[0]
                    if norm in seen_src:
                        img.drop_tag()
                        continue
                    seen_src.add(norm)
        for link in root.xpath(".//a[@href]"):
            href = normalize_image_url(base_url, link.get("href"))
            if not re.match(r"^https?://", href):
                link.drop_tag()
                continue
            link.set("href", href)
            link.set("target", "_blank")
            link.set("rel", "noopener")
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


def download_image(url: str, cache: dict, referer: str | None = None) -> str:
    if not url:
        return ""
    now = time.time()
    entry = cache.get(url, {})
    cached_path = entry.get("path", "")
    cached_ts = float(entry.get("timestamp", 0) or 0)
    if cached_path and (now - cached_ts) <= IMAGE_CACHE_TTL:
        if os.path.exists(os.path.join(IMAGES_DIR, cached_path)):
            return cached_path
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
    filename = f"{name[:16]}{ext}"
    path = os.path.join(IMAGES_DIR, filename)
    try:
        with open(path, "wb") as handle:
            handle.write(data)
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
        with urllib.request.urlopen(req, timeout=20) as resp:
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
        else:
            nodes = root.xpath("//article")
        best_node = None
        best_len = 0
        for node in nodes:
            text = node.text_content() or ""
            if "ol.mingpao.com" in url:
                time_count = len(re.findall(r"\(\d{1,2}:\d{2}\)", text))
                if time_count >= 3:
                    continue
            length = len(text)
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


def fetch_oncc_list(url: str, feed_cache: dict) -> list[Item]:
    items: list[Item] = []
    payload, meta = fetch_with_cache(url, feed_cache)
    if meta:
        entry = feed_cache.get(url, {})
        if payload:
            entry["payload_b64"] = base64.b64encode(payload).decode("ascii")
        if meta.get("etag"):
            entry["etag"] = meta.get("etag")
        if meta.get("last_modified"):
            entry["last_modified"] = meta.get("last_modified")
        entry["timestamp"] = meta.get("timestamp", time.time())
        feed_cache[url] = entry
    if not payload:
        return items
    html_text = payload.decode("utf-8", errors="ignore")
    links = re.findall(r'href=\"(/hk/bkn/cnt/news/\d{8}/[^\"]+\.html)\"', html_text)
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
                category="news",
                summary=text,
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
            result.extend(filter_recent(rows, 2))
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
    money_units = {
        "萬元",
        "億元",
        "千元",
        "百萬",
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
    for title, body in items_texts:
        title_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,6}", title or ""))
        body_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,6}", body or ""))
        text_blob = f"{title}\n{body}"
        for ph in phrases:
            if ph in text_blob:
                counts[ph] = counts.get(ph, 0.0) + 4.0
        for token in title_tokens:
            if token in stopwords:
                continue
            if token in money_units:
                continue
            if re.search(r"\d", token):
                continue
            if any(ch in weak_chars for ch in token) and len(token) <= 2:
                continue
            score = 3.0
            if any(token.endswith(s) for s in entity_suffixes):
                score += 2.0
            if any(token.endswith(s) for s in place_suffixes):
                score += 1.5
            if len(token) >= 4:
                score += 0.5
            counts[token] = counts.get(token, 0.0) + score
        for token in body_tokens:
            if token in stopwords:
                continue
            if token in money_units:
                continue
            if re.search(r"\d", token):
                continue
            if any(ch in weak_chars for ch in token) and len(token) <= 2:
                continue
            score = 1.0
            if any(token.endswith(s) for s in entity_suffixes):
                score += 1.5
            if any(token.endswith(s) for s in place_suffixes):
                score += 1.0
            counts[token] = counts.get(token, 0.0) + score
    sorted_tokens = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [t for t, _ in sorted_tokens[:limit]]

def get_category(source: str, category: str = "") -> str:
    if category:
        return category
    if source == "cnbeta":
        return "tech"
    if source in ("mingpao", "RTHK", "singtao", "oncc"):
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
    seen_cache: dict,
) -> None:
    build_id = str(int(time.time()))
    now_hkt = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%Y-%m-%d %H:%M")
    latest_pub = ""
    cards = []
    keyword_texts: list[tuple[str, str]] = []
    now_dt = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    for idx, item in enumerate(items, start=1):
        content = item.summary
        content_html = ""
        image_url = item.rss_image
        if item.link and item.source != "oncc":
            fulltext, og_image = extract_fulltext_and_image(item.link, fulltext_cache)
            if fulltext:
                content = fulltext
            if og_image and not is_generic_image(og_image, item.link):
                if (not image_url) or is_generic_image(image_url, item.link):
                    image_url = og_image
            full_html = extract_full_html(item.link, fullhtml_cache, image_cache)
            if full_html:
                content_html = full_html
        content = clean_content_text(to_trad_if_cnbeta(item.source, strip_html(content)))
        content = re.sub(r"。(」)", r"。\1\n", content)
        content = re.sub(r"。(?!」)", "。\n", content)
        keyword_texts.append((item.title, content))
        if not content_html:
            content_html = "<br>".join(html.escape(content).splitlines())
        if item.source == "singtao" and image_url:
            m = re.search(r"<img[^>]+src=['\"]([^'\"]+)['\"]", content_html)
            if m:
                hero_norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", image_url).split("?")[0]
                body_norm = re.sub(r"/f/\\d+p0/0x0/[^/]+/", "/", m.group(1)).split("?")[0]
                if hero_norm == body_norm:
                    image_url = ""
        if item.pub_dt:
            pub_text = item.pub_dt.astimezone(ZoneInfo("Asia/Hong_Kong")).strftime(
                "%Y-%m-%d %H:%M HKT"
            )
        else:
            pub_text = item.pub_text or ""
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
        hero_html = ""
        hero_caption = ""
        if image_url:
            image_url = normalize_image_url(item.link, image_url)
            local_name = download_image(image_url, image_cache, item.link)
            if local_name:
                hero_url = f"images/{local_name}?v={build_id}"
            else:
                hero_url = f"{image_url}?v={build_id}"
            if item.source != "cnbeta":
                hero_html = f"<img class='hero' src='{html.escape(hero_url)}' alt=''>"
        seen_class = " seen" if item.link and item.link in seen_cache else ""
        cards.append(
            """
      <article class="card{seen_class} category-{category} {age_class}" data-source="{source}" data-category="{category}" data-title="{title}">
        <header class="card-head">
          <span class="index">{idx:02d}</span>
          <div>
            <h2>{title}</h2>
            <div class="meta-row">
              <span class="tag" data-link="{link}">{source}</span>
              <span class="cat cat-{category}">{category_label}</span>
              <span class="{date_class}">{pub}</span>
            </div>
          </div>
        </header>
        {hero}
        {hero_note}
        <div class="content">{content}</div>
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
                category_label="新聞" if category == "news" else ("科技" if category == "tech" else "娛樂"),
                age_class=age_class,
            )
        )

    build_ts = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%Y-%m-%d %H:%M:%S")
    if MIXED_MODE:
        meta_line = (
            f"RTHK/Mingpao 過去{int(lookback_hours)}小時｜"
            f"cnbeta 最近{CNBETA_LIMIT}則｜更新時間 {now_hkt}"
            + (f"｜最新新聞時間 {latest_pub}" if latest_pub else "")
        )
    else:
        meta_line = (
            f"過去{int(lookback_hours)}小時共{len(items)}則｜更新時間 {now_hkt}"
            + (f"｜最新新聞時間 {latest_pub}" if latest_pub else "")
        )

    keywords = extract_keywords(keyword_texts, 10)
    keyword_html = "".join(
        f"<span class=\"kw\" data-kw=\"{html.escape(k)}\">{html.escape(k)}</span>"
        for k in keywords
    )

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
    }}
    header.site {{
      padding: 24px 16px 8px;
      text-align: center;
    }}
    header.site h1 {{
      font-size: 28px;
      margin: 0 0 6px;
    }}
    header.site .meta {{
      color: var(--muted);
      font-size: 14px;
    }}
    .toolbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(246, 242, 234, 0.95);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--border);
      padding: 12px 16px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      justify-content: center;
    }}
    .toolbar input {{
      flex: 1 1 260px;
      max-width: 360px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: #fff;
      font-size: 14px;
    }}
    .filters {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: center;
    }}
    .filters.secondary {{
      display: none;
    }}
    .submeta {{
      width: 100%;
      text-align: center;
      color: var(--muted);
      font-size: 12px;
    }}
    .keywords {{
      width: 100%;
      display: flex;
      gap: 8px;
      flex-wrap: nowrap;
      justify-content: center;
      overflow-x: auto;
      padding-bottom: 2px;
      scrollbar-width: thin;
    }}
    .kw {{
      border: 1px dashed var(--border);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
      background: #fff7ee;
    }}
    .kw:hover {{
      border-color: var(--accent);
    }}
    .chip {{
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 12px;
      font-size: 12px;
      background: #fff;
      cursor: pointer;
      user-select: none;
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
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 16px;
      display: grid;
      gap: 16px;
    }}
    .card {{
      background: var(--card);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 10px 24px var(--shadow);
      border: 1px solid var(--border);
    }}
    .card.seen {{
      filter: saturate(0.95);
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
      grid-template-columns: auto 1fr;
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
      font-size: 20px;
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
      font-size: 12px;
      cursor: pointer;
    }}
    .date {{
      color: var(--accent);
      font-size: 12px;
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
    }}
    .refresh-btn:hover {{
      border-color: var(--accent);
    }}
    .hero {{
      width: 100%;
      max-height: 360px;
      object-fit: cover;
      border-radius: 12px;
      margin: 10px auto;
      display: block;
    }}
    .content {{
      font-size: 15px;
      color: #262626;
      white-space: normal;
    }}
    .content img {{
      display: block;
      margin-left: auto;
      margin-right: auto;
      max-width: 80%;
      max-height: 360px;
      object-fit: contain;
      border-radius: 12px;
      margin-top: 8px;
      margin-bottom: 8px;
      height: auto;
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
        font-size: 18px;
      }}
      .toolbar {{
        padding: 10px 12px;
      }}
    }}
  </style>
</head>
<body>
  <header class="site">
    <h1>即時焦點</h1>
    <div class="meta">{meta_line}｜<button class="refresh-btn" id="refresh" title="更新">⟳</button></div>
  </header>
  <div class="toolbar">
    <input id="search" type="search" placeholder="搜尋標題或內容…">
    <div class="filters">
      <span class="chip active" data-category="all">全部</span>
      <span class="chip" data-category="news">新聞</span>
      <span class="chip" data-category="ent">娛樂</span>
      <span class="chip" data-category="tech">科技</span>
    </div>
    <div class="filters secondary" id="news-sources">
      <span class="chip active" data-source="all">全部</span>
      <span class="chip" data-source="RTHK">RTHK</span>
      <span class="chip" data-source="mingpao">Mingpao</span>
      <span class="chip" data-source="oncc">ON.cc</span>
      <span class="chip" data-source="singtao">Singtao</span>
    </div>
    <div class="keywords">{keyword_html}</div>
  </div>
  <main id="list">
    {"".join(cards) if cards else '<div class="empty">近 12 小時內冇新項目。</div>'}
  </main>
  <footer class="site-footer">生成時間 {build_ts} HKT</footer>
  <script>
    const categoryChips = document.querySelectorAll('.filters:not(.secondary) .chip');
    const sourceChips = document.querySelectorAll('.filters.secondary .chip');
    const cards = document.querySelectorAll('.card');
    const search = document.getElementById('search');
    const refreshBtn = document.getElementById('refresh');
    const refreshMs = {max(60, int(refresh_seconds))} * 1000;
    let lastAuto = Date.now();
    const newsSources = document.getElementById('news-sources');
    let activeCategory = 'all';
    let activeSource = 'all';

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

    categoryChips.forEach(chip => {{
      chip.addEventListener('click', () => {{
        categoryChips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeCategory = chip.dataset.category || 'all';
        if (activeCategory === 'news') {{
          newsSources.style.display = 'flex';
        }} else {{
          newsSources.style.display = 'none';
          activeSource = 'all';
          sourceChips.forEach(c => c.classList.remove('active'));
          sourceChips[0].classList.add('active');
        }}
        applyFilter();
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
        window.location.reload();
      }});
    }}
    setInterval(() => {{
      if (window.scrollY <= 5 && (Date.now() - lastAuto) >= refreshMs) {{
        window.location.reload();
      }}
    }}, 1000);

    search.addEventListener('input', applyFilter);
    document.querySelectorAll('.kw').forEach(kw => {{
      kw.addEventListener('click', () => {{
        const word = kw.dataset.kw || '';
        if (!word) return;
        search.value = word;
        applyFilter();
        const first = Array.from(cards).find(c => c.style.display !== 'none');
        if (first) first.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }});
    }});

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
    for url in urls:
        if "on.cc" in url:
            items.extend(fetch_oncc_list(url, feed_cache))
            continue
        payload, meta = fetch_with_cache(url, feed_cache)
        if meta:
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
            category = "news"
        elif "s00007.xml" in url:
            source = "mingpao"
            category = "ent"
        else:
            source = "mingpao"
            category = "news"
        try:
            items.extend(parse_items(payload, source, category))
        except Exception as exc:
            print(f"Parse failed ({url}): {exc}")
            continue
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
    seen_cache = load_json(SEEN_CACHE_PATH)
    feed_cache = gc_cache(feed_cache, CACHE_GC_TTL)
    image_cache = gc_cache(image_cache, CACHE_GC_TTL)
    fulltext_cache = gc_cache(fulltext_cache, CACHE_GC_TTL)
    fullhtml_cache = gc_cache(fullhtml_cache, CACHE_GC_TTL)
    seen_cache = gc_cache(seen_cache, SEEN_CACHE_TTL)

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
        seen_cache,
    )

    save_json(FEED_CACHE_PATH, feed_cache)
    save_json(FULLTEXT_CACHE_PATH, fulltext_cache)
    save_json(IMAGE_CACHE_PATH, image_cache)
    save_json(FULLHTML_CACHE_PATH, fullhtml_cache)
    save_json(SEEN_CACHE_PATH, mark_seen(seen_cache, items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
