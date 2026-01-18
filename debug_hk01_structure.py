
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

url = "https://www.hk01.com/社會新聞/60313636/渣馬-周潤發陪老友轉戰10公里-慢速完賽重享受-唔志在成"

async def analyze():
    feed_cache = {}
    image_cache = {}
    fetcher = AsyncFetcher(feed_cache, image_cache)
    
    print(f"Fetching {url}...")
    html_content = await fetcher.fetch_full_text(url)
    await fetcher.close()
    
    if not html_content:
        print("Fetch failed")
        return

    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_content, re.S)
    if not match:
        print("No JSON found")
        return

    data = json.loads(match.group(1))
    
    candidates = []
    def collect_candidates(obj):
        if isinstance(obj, dict):
            if 'blocks' in obj and isinstance(obj['blocks'], list) and len(obj['blocks']) > 0:
                candidates.append(obj)
            for v in obj.values():
                collect_candidates(v)
        elif isinstance(obj, list):
            for v in obj:
                collect_candidates(v)
    
    collect_candidates(data)
    
    if candidates:
        article = max(candidates, key=lambda x: len(x['blocks']))
        print(f"Inspecting Article Blocks ({len(article['blocks'])}):")
        
        for i, block in enumerate(article['blocks']):
            if i < 30 or i > 35: continue
            
            b_type = block.get('type') or block.get('blockType')
            b_data = block.get('data') or block
            
            if b_type == 'htmlTokens' or b_type == 'text':
                print(f"\n[Block {i}] Type: {b_type}")
                keys = list(b_data.keys())
                print(f"Keys: {keys}")
                
                txt = b_data.get('text')
                if txt:
                    print(f"Text Content: {txt[:100]}...")
                
                if 'htmlTokens' in b_data:
                    print("Contains htmlTokens!")
                    tokens = b_data['htmlTokens']
                    # Print more tokens
                    print(json.dumps(tokens[:10], ensure_ascii=False, indent=2))
            
            elif b_type == 'image':
                 print(f"\n[Block {i}] Type: image")
                 print(json.dumps(b_data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(analyze())
