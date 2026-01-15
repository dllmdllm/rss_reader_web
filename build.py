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
        items = parse_items(content, u)
        
        # RTHK Category Override based on Feed URL
        if "rthk" in u:
            r_cat = "news"
            if "cinternational" in u or "greaterchina" in u:
                r_cat = "intl"
            # clocal defaults to news
            
            for i in items:
                i.category = r_cat
                
        return items

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
    
    # --- HELPER FUNCTIONS ---
    def map_cat(url):
        u = url.lower()
        if "entertainment" in u: return "ent"
        if "/ent/" in u or "_ent" in u or "-ent-" in u: return "ent"
        if "s00007" in u: return "ent"
        if "tech" in u or "unwire" in u or "epc" in u or "9to5" in u: return "tech"
        if "intl" in u or "international" in u or "world" in u: return "intl"
        if "china" in u: return "intl"
        return "news"

    def clean_source_name(txt):
        if not txt: return "News"
        t = txt.lower()
        if "mingpao" in t: return "MingPao"
        if "hk01" in t: return "HK01"
        if "rthk" in t: return "RTHK"
        if "on.cc" in t: return "on.cc"
        if "singtao" in t or "stheadline" in t: return "Singtao"
        if "cnbeta" in t: return "CNBeta"
        if "unwire" in t: return "unwire.hk"
        if "hkepc" in t: return "HKEPC"
        if "9to5" in t: return "9to5Mac"
        if "witness" in t: return "The Witness"
        return "News"

    def process_image_url(url):
        return url

    # Pre-process basic fields
    from rss_core.parser import to_trad
    for item in valid_items:
        item.source = clean_source_name(item.source)
        if not item.category:
            item.category = map_cat(item.link)
        if "cnbeta" in item.link or "cnbeta" in item.source.lower():
            item.title = to_trad(item.title)

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
            main_img = cached.get("main_image", "") # NEW
            if full_html: item.content_html = full_html
            if main_img: item.rss_image = main_img # NEW
            elif imgs: item.rss_image = imgs[0] # Fallback
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
                        "main_image": main_img, # NEW
                        "images": all_imgs,
                        "ts": int(time.time())
                    }
                except Exception: pass
        return item

    # Parallelize Enrichment
    print(">>> [Build] Enriching items (Fulltext & Images) in parallel...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Use as_completed to progress, but we need to map results back to ordered list
        futures = {executor.submit(enrich_job, item): item for item in valid_items}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Enrichment error: {e}")

    # Final Construction (Ordered)
    final_data_list = []
    for item in valid_items:
        # 3. Process Hero Image (already enriched in job)
        local_hero = None
        if item.rss_image:
            local_hero = process_image_url(item.rss_image)
        
        # 4. Construct JSON Dict
        item_dict = {
            "title": item.title,
            "id": None, # Will be set in JS
            "link": item.link,
            "pub_ts": int(item.pub_dt.timestamp()),
            "pub_fmt": item.pub_dt.strftime("%m-%d %H:%M"),
            "source": item.source,
            "category": item.category,
            "hero_img": local_hero or "",
            "content": item.content_html or item.content_text or "",
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
    # Get top 20 items, format: "[time] Title", pass as dict with link for ID
    marquee_items = [
        {
            "text": f"✦ [{i['pub_fmt']}] {i['title']}", 
            "link": i['link']
        } 
        for i in final_data_list[:20]
    ]
    
    import random
    random.shuffle(marquee_items)
    
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
