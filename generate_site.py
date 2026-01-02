#!/usr/bin/env python3
import argparse
import base64
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
from urllib.parse import urljoin, urlparse

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo


DEFAULT_URLS = ",".join(
    [
        "https://news.rthk.hk/rthk/news/rss/c_expressnews_clocal.xml",
        "https://news.mingpao.com/rss/ins/all.xml",
    ]
)
DEFAULT_LOOKBACK_HOURS = 12
DEFAULT_REFRESH_SECONDS = 600
DEFAULT_MAX_ITEMS = 200
DEFAULT_THREADS = 4

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SITE_DIR = os.path.join(os.path.dirname(__file__), "site")
IMAGES_DIR = os.path.join(SITE_DIR, "images")
FEED_CACHE_PATH = os.path.join(DATA_DIR, "feed_cache.json")
FULLTEXT_CACHE_PATH = os.path.join(DATA_DIR, "fulltext_cache.json")
IMAGE_CACHE_PATH = os.path.join(DATA_DIR, "image_cache.json")

FULLTEXT_CACHE_TTL = 6 * 60 * 60
IMAGE_CACHE_TTL = 24 * 60 * 60


@dataclass
class Item:
    title: str
    link: str
    pub_dt: datetime | None
    pub_text: str
    source: str
    summary: str


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
        if "相關字詞" in line or "編輯推介" in line or "熱門HOTPICK" in line:
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


def download_image(url: str, cache: dict) -> str:
    if not url:
        return ""
    now = time.time()
    entry = cache.get(url, {})
    cached_path = entry.get("path", "")
    cached_ts = float(entry.get("timestamp", 0) or 0)
    if cached_path and (now - cached_ts) <= IMAGE_CACHE_TTL:
        return cached_path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
        name = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        ext = guess_image_ext(url, content_type)
        filename = f"{name[:24]}{ext}"
        path = os.path.join(IMAGES_DIR, filename)
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
            return normalize_image_url(base_url, child.attrib.get("url", "") or "")
        if "media" in tag and tag.endswith("content"):
            return normalize_image_url(base_url, child.attrib.get("url", "") or "")
    desc = find_text(item, "description") or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc)
    if match:
        return normalize_image_url(base_url, match.group(1))
    match = re.search(r'<img[^>]+data-src=["\']([^"\']+)["\']', desc)
    if match:
        return normalize_image_url(base_url, match.group(1))
    return ""


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
                return cached_text, cached_image
    try:
        from lxml import html as lxml_html
    except Exception:
        return "", ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return cached_text, cached_image

    image_url = ""
    try:
        root_full = lxml_html.fromstring(raw)
        og_image = root_full.xpath("//meta[@property='og:image']/@content")
        if og_image:
            image_url = og_image[0].strip()
        if not image_url:
            twitter_image = root_full.xpath("//meta[@name='twitter:image']/@content")
            if twitter_image:
                image_url = twitter_image[0].strip()
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


def parse_items(payload: bytes | str, source: str) -> list[Item]:
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
            items.append(
                Item(
                    title=strip_html(title),
                    link=link,
                    pub_dt=pub_dt,
                    pub_text=pub_text,
                    source=source,
                    summary=strip_html(summary),
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
                summary = lxml_text(item, "encoded") or lxml_text(item, "description")
                items.append(
                    Item(
                        title=strip_html(title),
                        link=link,
                        pub_dt=pub_dt,
                        pub_text=pub_text,
                        source=source,
                        summary=strip_html(summary),
                    )
                )
            return items
        except Exception:
            raise


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


def build_html(
    items: list[Item],
    output_path: str,
    lookback_hours: float,
    refresh_seconds: int,
    image_cache: dict,
    fulltext_cache: dict,
) -> None:
    now_hkt = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime("%Y-%m-%d %H:%M")
    latest_pub = ""
    cards = []
    for idx, item in enumerate(items, start=1):
        content = item.summary
        image_url = ""
        if item.link:
            fulltext, og_image = extract_fulltext_and_image(item.link, fulltext_cache)
            if should_use_fulltext(item.summary, fulltext):
                content = fulltext
            if og_image:
                image_url = og_image
        content = clean_content_text(strip_html(content))
        content = re.sub(r"。(」)", r"。\1\n", content)
        content = re.sub(r"。(?!」)", "。\n", content)
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
        hero_html = ""
        if image_url:
            image_url = normalize_image_url(item.link, image_url)
            local_name = download_image(image_url, image_cache)
            if local_name:
                hero_html = f"<img class='hero' src='images/{html.escape(local_name)}' alt=''>"
        cards.append(
            """
      <article class="card" data-source="{source}" data-title="{title}">
        <header class="card-head">
          <span class="index">{idx:02d}</span>
          <div>
            <h2>{title}</h2>
            <div class="meta-row">
              <span class="tag" data-link="{link}">{source}</span>
              <span class="{date_class}">{pub}</span>
            </div>
          </div>
        </header>
        {hero}
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
                content="<br>".join(html.escape(content).splitlines()),
            )
        )

    meta_line = (
        f"過去{int(lookback_hours)}小時共{len(items)}則｜更新時間 {now_hkt}"
        + (f"｜最新新聞時間 {latest_pub}" if latest_pub else "")
    )

    html_text = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="{max(60, int(refresh_seconds))}">
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
    .hero {{
      width: 100%;
      border-radius: 12px;
      margin: 10px 0;
    }}
    .content {{
      font-size: 15px;
      color: #262626;
      white-space: normal;
    }}
    .empty {{
      text-align: center;
      color: var(--muted);
      padding: 40px 16px;
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
    <div class="meta">{meta_line}</div>
  </header>
  <div class="toolbar">
    <input id="search" type="search" placeholder="搜尋標題或內容…">
    <div class="filters">
      <span class="chip active" data-source="all">全部</span>
      <span class="chip" data-source="RTHK">RTHK</span>
      <span class="chip" data-source="mingpao">Mingpao</span>
    </div>
  </div>
  <main id="list">
    {"".join(cards) if cards else '<div class="empty">近 12 小時內冇新項目。</div>'}
  </main>
  <script>
    const chips = document.querySelectorAll('.chip');
    const cards = document.querySelectorAll('.card');
    const search = document.getElementById('search');
    let activeSource = 'all';

    function applyFilter() {{
      const q = (search.value || '').trim().toLowerCase();
      cards.forEach(card => {{
        const source = card.dataset.source;
        const text = (card.dataset.title + ' ' + card.textContent).toLowerCase();
        const sourceOk = activeSource === 'all' || source === activeSource;
        const textOk = !q || text.includes(q);
        card.style.display = sourceOk && textOk ? '' : 'none';
      }});
    }}

    chips.forEach(chip => {{
      chip.addEventListener('click', () => {{
        chips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        activeSource = chip.dataset.source;
        applyFilter();
      }});
    }});

    search.addEventListener('input', applyFilter);

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
        source = "RTHK" if "rthk" in url else "mingpao"
        try:
            items.extend(parse_items(payload, source))
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

    urls = [u.strip() for u in args.url.split(",") if u.strip()]
    items = fetch_all(urls, feed_cache)

    if args.lookback_hours > 0:
        items = filter_recent(items, args.lookback_hours)
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
    )

    save_json(FEED_CACHE_PATH, feed_cache)
    save_json(FULLTEXT_CACHE_PATH, fulltext_cache)
    save_json(IMAGE_CACHE_PATH, image_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
