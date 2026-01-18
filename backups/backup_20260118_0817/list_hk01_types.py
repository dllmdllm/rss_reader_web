
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def list_hk01_block_types():
    fetcher = AsyncFetcher({}, {})
    urls = [
        "https://www.hk01.com/article/1074929", # Hotel/Massage
        "https://www.hk01.com/article/1093155", # TVB Drama
        "https://www.hk01.com/article/1093758", # News
    ]
    
    unique_types = set()
    
    for url in urls:
        print(f"Fetching {url}...")
        html = await fetcher.fetch_full_text(url)
        if not html: continue
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if not match: continue
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
        if article:
            for b in article['blocks']:
                unique_types.add(b.get('type') or b.get('blockType'))
                
    print(f"All Unique Block Types: {unique_types}")
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(list_hk01_block_types())
