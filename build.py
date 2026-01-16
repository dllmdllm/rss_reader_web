import os
import json
import time
import argparse
import hashlib
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import jinja2
from concurrent.futures import ThreadPoolExecutor

# Data Processing Imports
from rss_core.config import (
    SITE_DIR, CACHE_DIR, FEED_CACHE_PATH, IMAGE_CACHE_PATH,
    FULLTEXT_CACHE_PATH, DEFAULT_LOOKBACK_HOURS, DEFAULT_MAX_ITEMS,
    DEFAULT_REFRESH_SECONDS
)
from rss_core.fetcher import AsyncFetcher
from rss_core.feed_parser import parse_items
from rss_core.utils import (
    load_json, save_json, ensure_dirs
)

def clean_content_text(text):
    if not text: return ""
    try:
        from lxml import html
        return html.fromstring(f"<div>{text}</div>").text_content()
    except Exception:
        import re
        return re.sub(r'<[^>]+>', '', text)

async def main():
    # 0. Setup
    ensure_dirs()
    parser = argparse.ArgumentParser()
    parser.add_argument("--feeds", default="feeds.json")
    parser.add_argument("--output", default=os.path.join(SITE_DIR, "index.html"))
    args = parser.parse_args()

    print(">>> [Build] Starting RSS Reader Build (Async Version)...")
    print(f">>> [Build] Timezone: Asia/Hong_Kong")

    # 1. Load Caches
    feed_cache = load_json(FEED_CACHE_PATH)
    image_cache = load_json(IMAGE_CACHE_PATH)
    fulltext_cache = load_json(FULLTEXT_CACHE_PATH)

    # 2. Extract Feeds
    fetcher = AsyncFetcher(feed_cache, image_cache)
    urls = []
    if os.path.exists(args.feeds):
        with open(args.feeds, "r") as f:
            data = json.load(f)
            if isinstance(data, list): urls = data
            elif isinstance(data, dict): urls = data.get("feeds", [])
    
    # 3. Fetch Items
    print(f">>> [Build] Fetching {len(urls)} feeds concurrently...")
    
    async def fetch_job(u):
        content, meta = await fetcher.fetch_url(u)
        if not content: return []
        
        # Run CPU-bound parsing in a thread to keep event loop responsive
        items = await asyncio.to_thread(parse_items, content, u)
        
        # RTHK Category Override
        if "rthk" in u:
            r_cat = "news"
            if "cinternational" in u or "greaterchina" in u:
                r_cat = "intl"
            for i in items:
                i.category = r_cat
        return items

    results = await asyncio.gather(*[fetch_job(u) for u in urls], return_exceptions=True)
    raw_items = []
    for res in results:
        if isinstance(res, list):
            raw_items.extend(res)
        else:
            print(f"Fetch error: {res}")

    print(f">>> [Build] Fetched {len(raw_items)} raw items.")

    # 4. Dedupe & Filter
    hk_tz = ZoneInfo("Asia/Hong_Kong")
    now = datetime.now(hk_tz)
    
    # helper for category mapping (moved up)
    def map_cat(url):
        u = url.lower()
        # MingPao Specifics
        if "s00001" in u: return "news"
        if "s00004" in u or "s00005" in u: return "intl"
        if "s00007" in u or "s00024" in u: return "ent"
        
        # Mapping for HK01, On.cc and general
        # Ent
        if any(x in u for x in ["/entertainment/", "/ent/", "/star/", "娛樂", "娱乐"]): return "ent"
        
        # Tech
        if any(x in u for x in ["tech", "unwire", "epc", "9to5", "cnbeta", "science", "/it/"]): return "tech"
        
        # Intl / China (Often categorized as Intl in HK context)
        if any(x in u for x in ["/intl/", "/international/", "/world/", "/china/", "/cn/", "两岸", "國際", "国际"]): return "intl"
        
        return "news"


    def clean_source_name(txt, url):
        # Combine source title and URL for robust matching
        # (e.g. HK01 feed title might be "香港01" which doesn't match "hk01" key, but URL does)
        search_str = (str(txt) + " " + str(url)).lower()
        
        mapping = {
            "mingpao": "MingPao", "hk01": "HK01", "rthk": "RTHK", "on.cc": "on.cc",
            "singtao": "Singtao", "stheadline": "Singtao", "cnbeta": "CNBeta",
            "unwire": "unwire.hk", "hkepc": "HKEPC", "9to5": "9to5Mac", "witness": "The Witness",
            "collective": "The Collective", "courtnews": "Court News"
        }
        for k, v in mapping.items():
            if k in search_str: return v
            
        return txt if txt else "News"

    from rss_core.parser import get_parser, to_trad

    tech_items = []
    other_items = []
    seen_links = set()
    
    cutoff_others = now - timedelta(hours=6)
    # Optional safety cutoff for Tech to prevent issues with very old items if feed is huge, 
    # but user asked for "latest 50", so we prioritize count. 
    # Let's still set a reasonable safety margin of 7 days for Tech just in case.
    cutoff_tech_safety = now - timedelta(days=7)

    for item in raw_items:
        try:
            if not item.pub_dt: continue
            if item.pub_dt.tzinfo is None:
                 item.pub_dt = item.pub_dt.replace(tzinfo=hk_tz)
            else:
                 item.pub_dt = item.pub_dt.astimezone(hk_tz)
            
            if item.pub_dt > now:
                item.pub_dt = now

            if item.link in seen_links: continue
            
            # Category Check
            # (If item.category is already set by parser override e.g. RTHK, use it, else map)
            cat = item.category if item.category else map_cat(item.link)
            
            # Filter Logic
            if cat == 'tech':
                if item.pub_dt < cutoff_tech_safety: continue
                seen_links.add(item.link)
                tech_items.append(item)
            else:
                if item.pub_dt < cutoff_others: continue
                seen_links.add(item.link)
                other_items.append(item)

        except Exception as e:
            continue

    # Tech: Keep latest 50
    tech_items.sort(key=lambda x: x.pub_dt, reverse=True)
    tech_items = tech_items[:50]
    
    # Others: Already time-filtered
    # Combine
    valid_items = tech_items + other_items
    valid_items.sort(key=lambda x: x.pub_dt, reverse=True)
    
    # Apply Metadata Cleaning (Source, Title, etc) to valid items
    for item in valid_items:
        item.source = clean_source_name(item.source, item.link)
        if not item.category: item.category = map_cat(item.link)
        if "cnbeta" in item.link or "cnbeta" in item.source.lower():
            item.title = to_trad(item.title)

    print(f">>> [Build] {len(valid_items)} items after filter & sort (Tech: {len(tech_items)}, Others: {len(other_items)}).")

    enrich_sem = asyncio.Semaphore(5)
    async def enrich_job(item):
        if item.link in fulltext_cache:
            cached = fulltext_cache[item.link]
            item.content_html = cached.get("content", "")
            imgs = cached.get("images", [])
            main_img = cached.get("main_image", "")
            # If cached title is real, use it
            if cached.get("title") and ("HK01" not in item.source or "Article" not in cached["title"]):
                 item.title = cached["title"] 
            if main_img: item.rss_image = main_img
            elif imgs: item.rss_image = imgs[0]
            if item.content_html: return

        async with enrich_sem:
            p = get_parser(item.link, fetcher)
            html_text = await fetcher.fetch_full_text(item.link)
            if not html_text:
                if "HK01" in item.source: print(f"[HK01] Fetch FAILED for {item.link}")
                return

            if "HK01" in item.source:
                # Basic logging
                pass

            try: 
                # Robust Title Fix for HK01
                if "HK01" in item.source:
                    try:
                        # Re-parse HTML just for meta
                        from lxml import html
                        tree = html.fromstring(html_text)
                        ot = tree.xpath('//meta[@property="og:title"]/@content')
                        if ot:
                            item.title = ot[0].split("｜")[0].split("|")[0].strip()
                        else:
                            tt = tree.xpath('//title/text()')
                            if tt:
                                item.title = tt[0].split("｜")[0].split("|")[0].strip()
                    except: pass

                c_html, main_img, all_imgs = p.parse(html_text, item.link)

                # Localize Hero Image
                if main_img and ("hk01.com" in main_img or "on.cc" in main_img or "mingpao" in main_img):
                    local_img = await fetcher.download_image(main_img, referer=item.link)
                    if local_img:
                        main_img = f"images/{local_img}"

                c_html = await p.clean_html(c_html, item.link, main_img=main_img)
                
                if c_html:
                    item.content_html = c_html
                    if main_img: item.rss_image = main_img
                
                fulltext_cache[item.link] = {
                    "content": c_html or "",
                    "main_image": main_img or "",
                    "images": all_imgs or [],
                    "title": item.title,
                    "ts": int(time.time())
                }
            except Exception as e: 
                print(f"[Error] Enrich failed for {item.link}: {e}")

    print(">>> [Build] Enriching items (Fulltext & Images) asynchronously...")
    await asyncio.gather(*[enrich_job(item) for item in valid_items])

    # Final Construction
    final_data_list = []
    for item in valid_items:
        item_dict = {
            "title": item.title,
            "id": None,
            "link": item.link,
            "pub_ts": int(item.pub_dt.timestamp()),
            "pub_fmt": item.pub_dt.strftime("%m-%d %H:%M"),
            "source": item.source,
            "category": item.category,
            "hero_img": item.rss_image or "",
            "content": item.content_html or item.content_text or "",
            "search_text": clean_content_text(item.title + " " + (item.content_text or ""))[:1000] 
        }
        final_data_list.append(item_dict)

    # 6. Save Caches
    save_json(FEED_CACHE_PATH, feed_cache)
    save_json(IMAGE_CACHE_PATH, image_cache)
    save_json(FULLTEXT_CACHE_PATH, fulltext_cache)

    # 7. Render
    with open(os.path.join(SITE_DIR, "templates", "index_template.html"), "r", encoding="utf-8") as f:
        template_str = f.read()
    
    tmpl = jinja2.Template(template_str)
    marquee_items = [{"text": f"[{i['pub_fmt']}] {i['title']}", "link": i['link']} for i in final_data_list[:20]]
    import random
    random.shuffle(marquee_items)
    
    json_data = json.dumps(final_data_list, ensure_ascii=False).replace("</", "<\\/")
    
    output_html = tmpl.render(
        news_data_json=json_data, marquee_list=marquee_items,
        build_time=now.strftime("%H:%M"), refresh_seconds=DEFAULT_REFRESH_SECONDS
    )
    
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(output_html)
        
    await fetcher.close()
    print(">>> [Build] Done.")

if __name__ == "__main__":
    asyncio.run(main())
