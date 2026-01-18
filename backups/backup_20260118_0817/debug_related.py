import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def dump_related_block():
    fetcher = AsyncFetcher(feed_cache={}, image_cache={})
    test_urls = ["https://www.hk01.com/%E4%BA%BA%E6%B0%A3%E5%A8%9B%E6%A8%82/60313027/%E5%BC%B5%E5%B3%B0%E8%AD%B0%E5%9D%90%E7%9B%A34%E5%B9%B4-%E7%BE%9E%E6%8F%AD%E7%8D%84%E5%8F%8B%E8%BC%AA%E6%B5%81%E8%A7%A3%E6%B1%BA%E7%94%9F%E7%90%86%E9%9C%80%E8%A6%81-%E6%85%B6%E5%B9%B8%E4%B8%BB%E7%AE%A1%E5%BE%85%E4%BB%96%E4%B8%8D%E8%96%84"]
    
    for url in test_urls:
        html = await fetcher.fetch_full_text(url)
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if match:
            data = json.loads(match.group(1))
            def find_blocks(obj):
                if isinstance(obj, dict):
                    if 'blocks' in obj and isinstance(obj['blocks'], list):
                        for b in obj['blocks']: yield b
                    for v in obj.values(): yield from find_blocks(v)
                elif isinstance(obj, list):
                    for v in obj: yield from find_blocks(v)

            for block in find_blocks(data):
                b_type = block.get('type') or block.get('blockType')
                if b_type == 'related':
                    print(f"Related Block Data: {json.dumps(block, indent=2, ensure_ascii=False)}")
                    break
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(dump_related_block())
