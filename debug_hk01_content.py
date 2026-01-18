
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def main():
    fetcher = AsyncFetcher({}, {})
    url = "https://www.hk01.com/article/1091704" # Using a likely valid ID or I can fetch main page to find one
    
    # Let's try to get a fresh link from homepage first to be sure
    print("Fetching Homepage to find a fresh article...")
    content, _ = await fetcher.fetch_url("https://www.hk01.com")
    if isinstance(content, bytes): content = content.decode("utf-8", errors="ignore")
    
    match = re.search(r'href=["\'](/article/\d+[^"\']*)["\']', content)
    if match:
        url = "https://www.hk01.com" + match.group(1)
        print(f"Found Article: {url}")
    else:
        print(f"Using Default URL: {url}")

    print(f"Fetching {url}...")
    html, _ = await fetcher.fetch_url(url)
    if isinstance(html, bytes): html = html.decode("utf-8", errors="ignore")

    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if match:
        data = json.loads(match.group(1))
        
        # Locate article object
        article = None
        # Traverse to find 'blocks'
        def find_article_with_blocks(obj):
            if isinstance(obj, dict):
                if 'blocks' in obj and isinstance(obj['blocks'], list) and len(obj['blocks']) > 0:
                    return obj
                for k, v in obj.items():
                    res = find_article_with_blocks(v)
                    if res: return res
            elif isinstance(obj, list):
                for v in obj:
                    res = find_article_with_blocks(v)
                    if res: return res
            return None

        article = find_article_with_blocks(data)
        
        if article:
            print(f"Found Article Object. Blocks count: {len(article['blocks'])}")
            for i, block in enumerate(article['blocks']):
                b_type = block.get('type') or block.get('blockType')
                # print(f"Block {i}: Type='{b_type}'")
                
                if b_type == 'text':
                    print(f"\n[TEXT BLOCK FOUND] Index {i}")
                    if 'data' in block:
                        d = block['data']
                        print(f"  Data keys: {list(d.keys())}")
                        if 'text' in d:
                            print(f"  Text content (first 50): {d['text'][:50]}")
                        else:
                            print(f"  NO 'text' key in data! Full data: {d}")
                    else:
                        print(f"  NO 'data' key in block! Keys: {list(block.keys())}")
                elif b_type == 'paragraph':
                     print(f"\n[PARAGRAPH BLOCK FOUND] Index {i}")
                     print(f"  Block: {block}")
        else:
            print("No object with 'blocks' found in JSON.")
            
    else:
        print("No __NEXT_DATA__ found.")

    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(main())
