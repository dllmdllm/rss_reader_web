
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def main():
    fetcher = AsyncFetcher({}, {})
    
    print("Fetching Feed...")
    content, _ = await fetcher.fetch_url("https://www.hk01.com")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="ignore")
    
    print(f"Inspecting Feed: https://www.hk01.com")
    
    # Extract JSON
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', content, re.S)
    if match:
        data = json.loads(match.group(1))
        
        def find_any_article(obj, path="", parent=None):
            if isinstance(obj, dict):
                # Check directly for the tiny URL object signature inside 'data' field
                # We want to find the Item (parent) that has data.url
                
                # Check if current obj is the Item
                if 'data' in obj and isinstance(obj['data'], dict):
                    d = obj['data']
                    if 'url' in d and isinstance(d['url'], str) and '/article/' in d['url']:
                         print(f"\n[FOUND ITEM] at {path}")
                         print(f"  Item Keys: {list(obj.keys())}")
                         
                         if 'title' in obj:
                             print(f"  -> Title: {obj['title']}")
                         else:
                             print("  -> NO 'title' key found!")
                             print(f"  -> Potential Title Keys: {[k for k in obj.keys() if 'title' in k.lower()]}")

                         if 'publishTime' in obj:
                             print(f"  -> publishTime: {obj['publishTime']}")
                         
                         print(f"  -> data.url: {d['url']}")
                         return

                for k, v in obj.items():
                    find_any_article(v, f"{path}.{k}", obj)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                     find_any_article(v, f"{path}[{i}]", obj) 

        # Direct navigation to verify structure
        try:
             sections = data['props']['initialProps']['pageProps']['sections']
             print(f"Sections found: {len(sections)}")
             if len(sections) > 0 and 'items' in sections[0]:
                 items = sections[0]['items']
                 print(f"Items in section 0: {len(items)}")
                 if len(items) > 0:
                     first = items[0]
                     print("First Item Keys:", list(first.keys()))
                     if 'data' in first:
                         d = first['data']
                         print("First Item Data Keys:", sorted(list(d.keys())))
                         if 'publishTime' in d:
                             print(f"  -> publishTime Found in DATA: {d['publishTime']}")
                         
                         print(f"  -> redirectUrl: {d.get('redirectUrl')}")
                         
                         if 'title' in d:
                             print(f"  -> Title found in DATA: {d['title']}")
                         else:
                             print("  -> Title NOT in DATA")
                     else:
                         print("First Item has NO data key")
        except Exception as e:
            print(f"Direct navigation failed: {e}")

        # find_any_article(data)
    else:
        print("No __NEXT_DATA__ found.")

    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(main())
