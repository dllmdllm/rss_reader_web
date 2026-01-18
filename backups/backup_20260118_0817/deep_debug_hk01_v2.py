
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def deep_debug_hk01():
    fetcher = AsyncFetcher({}, {})
    url = "https://www.hk01.com/article/1074929"
    print(f"Checking {url}...")
    html = await fetcher.fetch_full_text(url)
    if not html:
        print("Failed to fetch")
        return
        
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        print("NEXT_DATA not found")
        return
        
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
        
    article = find_article(data)
    if not article:
        print("Article object not found")
        return
        
    print(f"Found article with {len(article['blocks'])} blocks")
    for i, b in enumerate(article['blocks']):
        b_type = b.get('type') or b.get('blockType')
        print(f"Block {i}: {b_type}")
        if b_type == 'image':
            print(f"  Image Block Data: {json.dumps(b, indent=2, ensure_ascii=False)[:500]}...")
            # Check for nested image data
            img_data = b.get('data', {}).get('image') or b.get('image')
            if img_data:
                print(f"  Actual Image Keys: {img_data.keys()}")
                print(f"  URL: {img_data.get('originalUrl') or img_data.get('url')}")
    
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(deep_debug_hk01())
