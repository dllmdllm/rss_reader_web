
import requests
import re
import json
import sys

# Target URL (Real article likely to have images)
url = "https://www.hk01.com/社會新聞/60313636/渣馬-周潤發陪老友轉戰10公里-慢速完賽重享受-唔志在成"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

print(f"Fetching {url}...")
try:
    resp = requests.get(url, headers=headers, timeout=10)
    text = resp.text
except Exception as e:
    print(f"Fetch Error: {e}")
    sys.exit(1)

# Check NEXT DATA
match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.S)
if match:
    print("Found NEXT_DATA")
    try:
        data = json.loads(match.group(1))
        
        # Helper to find article
        def find_article(obj, path=""):
            if isinstance(obj, dict):
                if 'blocks' in obj and isinstance(obj['blocks'], list):
                    print(f"FOUND BLOCKS at path: {path}")
                    # Print types of blocks found
                    types = [b.get('type') or b.get('blockType') for b in obj['blocks']]
                    print(f"Block Types: {types}")
                    
                    # Dump first few image blocks
                    for b in obj['blocks']:
                        b_type = b.get('type') or b.get('blockType')
                        if b_type == 'image':
                            print(f"IMAGE BLOCK: {json.dumps(b, indent=2, ensure_ascii=False)}")
                        elif b_type == 'gallery':
                            print(f"GALLERY BLOCK: {json.dumps(b, indent=2, ensure_ascii=False)}")
                    return obj
                    
                for k, v in obj.items():
                    res = find_article(v, path + "." + k)
                    if res: return res
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    res = find_article(v, path + f"[{i}]")
                    if res: return res
            return None

        article = find_article(data)
        if not article:
            print("No 'blocks' found in JSON")
            
    except Exception as e:
        print(f"JSON Parse Error: {e}")
else:
    print("No NEXT_DATA script found")

# Fallback Check: HTML Image tags
print("\n--- HTML Image Check ---")
from lxml import html
doc = html.fromstring(text)
imgs = doc.xpath("//article//img")
print(f"Found {len(imgs)} imgs in <article>")
for img in imgs[:5]:
    print(f"Img: src={img.get('src')} data-src={img.get('data-src')}")
