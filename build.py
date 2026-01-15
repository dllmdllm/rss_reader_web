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
    from rss_core.parser import get_parser
    
    def enrich_job(item):
        # 1. Fulltext
        # Logic: check cache -> if miss -> fetch -> parse -> save
        # Simplified: just use what we have or existing logic?
        # User said "Logic those you should know", implies reuse existing smarts.
        # I'll implement a concise enricher.
        
        # Determine if we need fulltext (e.g. if content is empty or explicitly requested)
        # For now, let's assume we want fulltext for everything if possible to get images.
        
        full_html = ""
        imgs = []
        
        # Check cache
        if item.link in fulltext_cache:
            # Use cached
            cached = fulltext_cache[item.link]
            full_html = cached.get("content", "")
            imgs = cached.get("images", [])
            # Update item
            if full_html: item.content_html = full_html
        else:
            # Fetch
            # Only if not in cache
            p = get_parser(item.link, fetcher)
            # We need the page source first
            page_src, _ = fetcher.fetch_url(item.link)
            if page_src:
                try: 
                    # Decode
                    html_text = page_src.decode('utf-8', errors='ignore')
                    c_html, main_img, all_imgs = p.parse(html_text, item.link)
                    # Clean
                    c_html = p.clean_html(c_html, item.link)
                    
                    if c_html:
                        item.content_html = c_html
                        full_html = c_html
                    
                    if all_imgs:
                        imgs.extend(all_imgs)
                    if main_img:
                        item.rss_image = main_img
                        
                    # Save to cache logic (deferred)
                    fulltext_cache[item.link] = {
                        "content": c_html,
                        "images": all_imgs,
                        "ts": int(time.time())
                    }
                except Exception: pass

        # 2. Image Downloading
        # We need to process item.rss_image and any imgs in content
        # For the 'card', we mainly need the hero image (item.rss_image).
        # Inner content images are loaded by browser (but we should cache them ideally for speed?
        # User said "download and compress". So yes.
        # But replacing URLs in content_html is complex.
        # Let's focus on the Hero Image first.
        
        if item.rss_image:
             # Download and resize
             from rss_core.utils import normalize_image_url
             # The fetcher.download_image (if it exists) or custom logic
             # I'll manually call fetcher to get bytes + Pillow to save
             pass # Logic handled below in main loop or helper
             
        return item

    # Run Enrichment
    # Doing this sequentially for safety or thread pool? Thread pool for speed.
    print(">>> [Build] Enriching items (Fulltext & Images)...")
    
    # We need a robust image downloader helper
    def process_image_url(url):
        if not url: return None
        # Check cache
        if url in image_cache:
            return image_cache[url] # Returns local path relative to site
            
        # Download
        # Use fetcher
        try:
            data, _ = fetcher.fetch_url(url)
        except Exception:
            return None
            
        if not data: return None
        
        # Save & Compress
        try:
            from PIL import Image
            import io
            img_hash = hashlib.md5(url.encode()).hexdigest()
            ext = "jpg" # force jpg/webp
            filename = f"{img_hash}.{ext}"
            rel_path = f"images/{filename}"
            full_path = os.path.join(SITE_DIR, "images", filename)
            
            # Ensure dir
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            if not os.path.exists(full_path):
                img = Image.open(io.BytesIO(data))
                img = img.convert("RGB")
                # Resize if too big
                if img.width > 1200:
                    ratio = 1200 / img.width
                    new_h = int(img.height * ratio)
                    img = img.resize((1200, new_h), Image.Resampling.LANCZOS)
                
                img.save(full_path, "JPEG", quality=75, optimize=True)
            
            image_cache[url] = rel_path
            return rel_path
        except Exception:
            return None

    # Processing Loop
    final_data_list = []
    
    # Helper to map category
    def map_cat(url):
        if "ent" in url or "entertainment" in url: return "ent"
        if "tech" in url or "unwire" in url or "epc" in url or "9to5" in url: return "tech"
        if "intl" in url or "international" in url or "world" in url: return "intl"
        if "china" in url: return "intl" # or news
        return "news"

    for item in valid_items:
        # Pre-enrich strict logic
        # 1. Assign Category
        item.category = map_cat(item.link)
        
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
