
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher
from rss_core.parser import HK01Parser, RTHKParser

async def debug_sources():
    fetcher = AsyncFetcher({}, {})
    
    # HK01 Debug
    hk01_url = "https://www.hk01.com/article/1074929"
    print(f"--- HK01 Debug: {hk01_url} ---")
    html_hk01 = await fetcher.fetch_full_text(hk01_url)
    if html_hk01:
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_hk01, re.S)
        if match:
            data = json.loads(match.group(1))
            def find_article(obj):
                if isinstance(obj, dict):
                    if 'blocks' in obj and isinstance(obj['blocks'], list): return obj
                    for v in obj.values():
                        res = find_article(v)
                        if res: return res
                elif isinstance(obj, list):
                    for v in obj:
                        res = find_article(v)
                        if res: return res
                return None
            art = find_article(data)
            if art:
                print(f"Article blocks found: {len(art['blocks'])}")
                for i, b in enumerate(art['blocks'][:5]):
                    print(f"Block {i} keys: {b.keys()}")
                    if 'type' in b: print(f"  Type: {b['type']}")
                    if 'data' in b: print(f"  Data keys: {b['data'].keys()}")
            else:
                print("Article object NOT found in JSON.")
    
    # RTHK Debug
    rthk_url = "https://news.rthk.hk/rthk/ch/component/k2/1788755-20250116.htm"
    print(f"\n--- RTHK Debug: {rthk_url} ---")
    html_rthk = await fetcher.fetch_full_text(rthk_url)
    if html_rthk:
        import lxml.html
        tree = lxml.html.fromstring(html_rthk)
        # Try finding ANY img to see classes
        all_imgs = tree.xpath("//img")
        print(f"Total imgs in page: {len(all_imgs)}")
        for img in all_imgs[:10]:
            print(f"Img: src={img.get('src', '')[:50]}, class={img.get('class', '')}")
            
        # Check specific containers
        containers = tree.xpath("//div[contains(@class, 'item')]")
        print(f"Found {len(containers)} div.item... containers.")

    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(debug_sources())
