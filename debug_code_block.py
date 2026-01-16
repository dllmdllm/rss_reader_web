
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def deep_debug_hk01():
    fetcher = AsyncFetcher({}, {})
    url = "https://www.hk01.com/article/1074929"
    html = await fetcher.fetch_full_text(url)
    data = json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S).group(1))
    
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
    for b in article['blocks']:
        if b.get('blockType') == 'code':
            print(f"Code Block Found: {json.dumps(b, indent=2, ensure_ascii=False)[:1000]}")
            break
            
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(deep_debug_hk01())
