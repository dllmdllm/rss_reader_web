
import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher

async def main():
    fetcher = AsyncFetcher({}, {})
    print("Fetching HK01...")
    content, _ = await fetcher.fetch_url("https://www.hk01.com")
    if isinstance(content, bytes): content = content.decode("utf-8", errors="ignore")
    
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', content, re.S)
    if match:
        data = json.loads(match.group(1))
        
        # Replicate the extraction logic from feed_parser.py to find what "Item 6" & "Item 7" are
        items_found = []
        
        def find_items(obj):
            if isinstance(obj, dict):
                # Check for Item-like structure
                if 'data' in obj and isinstance(obj['data'], dict):
                    d = obj['data']
                    # Check URL
                    url = d.get('url') or d.get('originalUrl') or d.get('publishUrl')
                    title = obj.get('title') or d.get('title')
                    
                    if title:
                         items_found.append(obj)
                
                for k, v in obj.items():
                    find_items(v)
            elif isinstance(obj, list):
                for v in obj:
                    find_items(v)

        # Simplify: Just iterate sections as the parser likely does
        try:
             sections = data['props']['initialProps']['pageProps']['sections']
             all_items = []
             for sec in sections:
                 if 'items' in sec:
                     all_items.extend(sec['items'])
             
             print(f"Total items found in sections: {len(all_items)}")
             
             for i, item in enumerate(all_items):
                 if i < 5 or i > 7: continue # User said item 6 and 7 (index 5 and 6)
                 
                 title = item.get('title')
                 data = item.get('data', {})
                 if not title and 'title' in data:
                     title = data['title']
                 
                 print(f"\n--- Item {i+1} ---")
                 print(f"Title: {title}")
                 print(f"Type: {item.get('type')}")
                 print(f"Item Keys: {list(item.keys())}")
                 print(f"Data Keys: {list(data.keys())}")
                 print(f"Full Data: {str(data)[:300]}")
                 if 'isSponsored' in data:
                     print(f"Data.isSponsored: {data['isSponsored']}")
                 if 'category' in data:
                     print(f"Data.category: {data['category']}")
                 if 'campaign' in data:
                      print(f"Data.campaign: {data['campaign']}")

        except Exception as e:
            print(f"Error: {e}")

    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(main())
