import os
import json
import time
import argparse
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import jinja2

# Data Processing Imports
from rss_core.config import (
    SITE_DIR, CACHE_DIR, FEED_CACHE_PATH, IMAGE_CACHE_PATH,
    FULLTEXT_CACHE_PATH, DEFAULT_LOOKBACK_HOURS, DEFAULT_MAX_ITEMS,
    DEFAULT_REFRESH_SECONDS
)
from rss_core.fetcher import Fetcher
from rss_core.feed_parser import parse_items
from rss_core.utils import (
    load_json, save_json, ensure_dirs
)

def clean_content_text(text):
    if not text: return ""
    try:
        # Use lxml for robust stripping
        from lxml import html
        # Wrap in div to handle fragments
        return html.fromstring(f"<div>{text}</div>").text_content()
    except Exception:
        # Fallback to simple regex if lxml fails (e.g. strict parsing error)
        import re
        return re.sub(r'<[^>]+>', '', text)



# We can reuse the logic from previous build.py loosely but make it cleaner

def main():
    # 0. Setup
    ensure_dirs()
    parser = argparse.ArgumentParser()
    parser.add_argument("--feeds", default="feeds.json")
    parser.add_argument("--output", default=os.path.join(SITE_DIR, "index.html"))
    args = parser.parse_args()

    print(">>> [Build] Starting RSS Reader Build...")
    print(f">>> [Build] Timezone: Asia/Hong_Kong")

    # 1. Load Caches
    feed_cache = load_json(FEED_CACHE_PATH)
    image_cache = load_json(IMAGE_CACHE_PATH)
    fulltext_cache = load_json(FULLTEXT_CACHE_PATH)

    # 2. Extract Feeds
    fetcher = Fetcher(feed_cache, image_cache)
    urls = []
    if os.path.exists(args.feeds):
        with open(args.feeds, "r") as f:
            data = json.load(f)
            if isinstance(data, list): urls = data
            elif isinstance(data, dict): urls = data.get("feeds", [])
    
    # 3. Fetch Items
    print(f">>> [Build] Fetching {len(urls)} feeds...")
    # Using a simplified fetch logic inline or reuse existing if easy?
    # Let's verify if 'parse_items' does fetching. No, 'parse_items' parses XML.
    # We need the fetching loop. I'll reimplement a clean concurrent fetch loop here 
    # or better, use the one from the backup if it was in build.py?
    # Actually, I'll write a clean one using ThreadPoolExecutor here to be sure.

    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    raw_items = []
    
    def fetch_job(u):
        content, meta = fetcher.fetch_url(u)
        if not content: return []
        # Parse
        return parse_items(content, u)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_job, u): u for u in urls}
        for future in as_completed(futures):
            try:
                items = future.result()
                raw_items.extend(items)
            except Exception as e:
                print(f"Error fetching: {e}")

    print(f">>> [Build] Fetched {len(raw_items)} raw items.")

    # 4. Dedupe & Filter
    # Sort by Date desc
    # Fix timestamps first
    hk_tz = ZoneInfo("Asia/Hong_Kong")
    now = datetime.now(hk_tz)
    cutoff = now - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    
    valid_items = []
    seen_links = set()


    
    for item in raw_items:
        try:
            # Normalize date
            if not item.pub_dt: continue
            # Convert to HK time if needed or just compare aware objects
            if item.pub_dt.tzinfo is None:
                 item.pub_dt = item.pub_dt.replace(tzinfo=hk_tz)
            else:
                 item.pub_dt = item.pub_dt.astimezone(hk_tz)
                 
            if item.pub_dt < cutoff: continue
            
            if item.link in seen_links: continue
            seen_links.add(item.link)
            valid_items.append(item)
        except Exception as e:
            print(f"Skipping bad item: {e}")
            continue


    # Sort
    valid_items.sort(key=lambda x: x.pub_dt, reverse=True)
    valid_items = valid_items[:DEFAULT_MAX_ITEMS]
    
    print(f">>> [Build] {len(valid_items)} items after filter & sort.")

    # 5. Enrich (Images & Fulltext)
    # This is critical. We need to download images locally.
    # The 'Fetcher' class in rss_core handles caching, but 'enrich' logic specifically
    # calling 'download_image' logic (which might be in utils or fetcher?)
    # Let's peek at rss_core/fetcher.py again? No need, I recall fetch_image logic.
    # I will iterate and ensure images are downloaded using fetcher.
    
    # Also reuse Fulltext cache
    # Logic: If content is short, try to fetch fulltext via Parser (rss_core.parser)
    from rss_core.parser import get_parser, to_trad
    
    def enrich_job(item):
        full_html = ""
        imgs = []
        if item.link in fulltext_cache:
            cached = fulltext_cache[item.link]
            full_html = cached.get("content", "")
            imgs = cached.get("images", [])
            if full_html: item.content_html = full_html
        else:
            p = get_parser(item.link, fetcher)
            page_src, _ = fetcher.fetch_url(item.link)
            if page_src:
                try: 
                    html_text = page_src.decode('utf-8', errors='ignore')
                    c_html, main_img, all_imgs = p.parse(html_text, item.link)
                    c_html = p.clean_html(c_html, item.link)
                    if c_html:
                        item.content_html = c_html
                        full_html = c_html
                    if all_imgs: imgs.extend(all_imgs)
                    if main_img: item.rss_image = main_img
                    fulltext_cache[item.link] = {
                        "content": c_html,
                        "images": all_imgs,
                        "ts": int(time.time())
                    }
                except Exception: pass
        return item

    print(">>> [Build] Enriching items (Fulltext & Images)...")
    
    def process_image_url(url):
        if not url: return None
        if url in image_cache: return image_cache[url]
        try:
            data, _ = fetcher.fetch_url(url)
        except Exception: return None
        if not data: return None
        try:
            from PIL import Image
            import io
            img_hash = hashlib.md5(url.encode()).hexdigest()
            ext = "jpg"
            filename = f"{img_hash}.{ext}"
            rel_path = f"images/{filename}"
            full_path = os.path.join(SITE_DIR, "images", filename)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if not os.path.exists(full_path):
                img = Image.open(io.BytesIO(data))
                img = img.convert("RGB")
                if img.width > 1200:
                    ratio = 1200 / img.width
                    new_h = int(img.height * ratio)
                    img = img.resize((1200, new_h), Image.Resampling.LANCZOS)
                img.save(full_path, "JPEG", quality=75, optimize=True)
            image_cache[url] = rel_path
            return rel_path
        except Exception: return None

    final_data_list = []
    
    def map_cat(url):
        if "ent" in url or "entertainment" in url: return "ent"
        if "tech" in url or "unwire" in url or "epc" in url or "9to5" in url: return "tech"
        if "intl" in url or "international" in url or "world" in url: return "intl"
        if "china" in url: return "intl"
        return "news"


    for item in valid_items:
        # Pre-enrich strict logic
        # 1. Assign Category
        item.category = map_cat(item.link)
        
        # 1.5 Translate Title if CNBeta
        if "cnbeta" in item.link or "cnbeta" in item.source:
             item.title = to_trad(item.title)

        
        # 2. Enrich content if needed
        enrich_job(item) 
        
        # 3. Process Hero Image
        local_hero = None
        if item.rss_image:
            local_hero = process_image_url(item.rss_image)
        
        # 4. Construct JSON Dict
        item_dict = {
            "title": item.title,
            "link": item.link,
            "pub_ts": int(item.pub_dt.timestamp()),
            "pub_fmt": item.pub_dt.strftime("%m-%d %H:%M"),
            "source": item.source,
            "category": item.category,
            "hero_img": local_hero or "",
            "content": item.content_html or item.content_text or "",
            # Extract simple text for search
            "search_text": clean_content_text(item.title + " " + (item.content_text or ""))[:1000] 
        }
        final_data_list.append(item_dict)

    print(f">>> [Build] Encoding {len(final_data_list)} items to JSON...")
    
    # 6. Save Caches
    save_json(FEED_CACHE_PATH, feed_cache)
    save_json(IMAGE_CACHE_PATH, image_cache)
    save_json(FULLTEXT_CACHE_PATH, fulltext_cache)

    # 7. Render
    # Load template
    with open(os.path.join(SITE_DIR, "templates", "index_template.html"), "r", encoding="utf-8") as f:
        template_str = f.read()
    
    tmpl = jinja2.Template(template_str)
    
    # Marquee Data
    # Get top 10 keywords or titles
    marquee_items = [i["title"] for i in final_data_list[:20]]
    
    # Serialize Data safely
    json_data = json.dumps(final_data_list, ensure_ascii=False)
    
    # Prevent Script Injection XSS
    json_data = json_data.replace("</", "<\\/")
    
    output_html = tmpl.render(
        news_data_json=json_data,
        marquee_list=marquee_items,
        build_time=now.strftime("%H:%M"),
        refresh_seconds=DEFAULT_REFRESH_SECONDS
    )
    
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(output_html)
        
    print(">>> [Build] Done.")
    return 0

if __name__ == "__main__":
    main()
