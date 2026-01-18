
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

url = "https://www.hk01.com/突發/60313824/紅花嶺郊遊徑行山漢突暈倒-友人施心肺復甦術-由直升機送院搶救"

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
    print(f"Found {len(candidates)} candidates.")

    if candidates:
        candidates.sort(key=lambda x: len(x['blocks']), reverse=True)
        best = candidates[0]
        
        text_block_count = 0
        for b in best['blocks']:
            bt = b.get('type') or b.get('blockType')
            if bt in ['text', 'htmlTokens']:
                text_block_count += 1
                
        print(f"Best Candidate Blocks: {len(best['blocks'])}")
        print(f"Text Blocks Count: {text_block_count}")
        
        if text_block_count == 0:
            print("CONFIRMED: Zero text blocks in JSON.")    
    
    print("-" * 20)
    print("Testing HTML Fallback Logic (simulated)...")
    
    from lxml import html
    tree = html.fromstring(html_content)
    
    # Simulate the fallback logic in parser.py
    # Remove scripts/styles
    for tag in tree.xpath('//script|//style|//iframe|//noscript'):
        tag.drop_tree()
        
    # Extract text from standard article body classes
    # Common HK01 body classes: .article-grid__content, .article-content, etc.
    # But let's check what we get.
    
    content_nodes = tree.xpath('//div[contains(@class, "article-grid__content")]//p | //div[contains(@class, "article-content")]//p | //article//p')
    
    extracted_text = []
    for node in content_nodes:
        txt = node.text_content().strip()
        if txt:
            extracted_text.append(txt)
            
    print(f"Extracted {len(extracted_text)} paragraphs from HTML.")
    for t in extracted_text[:5]:
        print(f"  P: {t[:50]}...")
        
    if any("12時42分" in t for t in extracted_text):
        print("SUCCESS: Found target text in HTML extraction.")
    else:
        print("FAILURE: Target text NOT FOUND in HTML extraction.")



if __name__ == "__main__":
    asyncio.run(analyze())
