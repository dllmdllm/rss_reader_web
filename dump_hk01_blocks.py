import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def dump_block_types():
    fetcher = AsyncFetcher(feed_cache={}, image_cache={})
    
    test_urls = [
        "https://www.hk01.com/%E4%BA%BA%E6%B0%A3%E5%A8%9B%E6%A8%82/60313027/%E5%BC%B5%E5%B3%B0%E8%AD%B0%E5%9D%90%E7%9B%A34%E5%B9%B4-%E7%BE%9E%E6%8F%AD%E7%8D%84%E5%8F%8B%E8%BC%AA%E6%B5%81%E8%A7%A3%E6%B1%BA%E7%94%9F%E7%90%86%E9%9C%80%E8%A6%81-%E6%85%B6%E5%B9%B8%E4%B8%BB%E7%AE%A1%E5%BE%85%E4%BB%96%E4%B8%8D%E8%96%84",
        "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%A8%9B%E6%A8%82/60313565/%E4%B8%AD%E5%B9%B42-%E9%99%B3%E4%BF%9E%E9%9C%8F%E7%A2%A9%E5%A3%AB%E7%95%A2%E6%A5%AD%E8%AD%89%E6%9B%B8%E6%B7%98%E5%AF%B6%E8%B3%A355%E8%9A%8A-%E6%80%A5%E5%88%AApo-%E8%B2%BB%E4%BA%8B%E5%98%88%E4%BA%82%E5%B7%B4%E9%96%89"
    ]
    
    unique_types = set()
    
    for url in test_urls:
        html = await fetcher.fetch_full_text(url)
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        if match:
            data = json.loads(match.group(1))
            
            def find_blocks(obj):
                if isinstance(obj, dict):
                    if 'blocks' in obj and isinstance(obj['blocks'], list):
                        for b in obj['blocks']:
                            yield b
                    for v in obj.values():
                        yield from find_blocks(v)
                elif isinstance(obj, list):
                    for v in obj:
                        yield from find_blocks(v)

            for block in find_blocks(data):
                b_type = block.get('type') or block.get('blockType')
                if b_type:
                    unique_types.add(b_type)

    print(f"Unique Block Types: {unique_types}")
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(dump_block_types())
