
import requests
import re
import json

url = "https://www.hk01.com/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

try:
    print(f"Fetching {url}...")
    resp = requests.get(url, headers=headers, timeout=10)
    text = resp.text

    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.S)
    if match:
        print("HK01 JSON Found.")
        data = json.loads(match.group(1))
        
        def find_articles_debug(obj):
            if isinstance(obj, dict):
                title = obj.get('title')
                url = obj.get('publishUrl') or obj.get('url')
                
                if title and isinstance(title, str):
                    if "會員" in title or "廣告" in title or "隱藏" in title:
                        print(f"!!! MATCH Found: [{title}] URL: {url}")
                
                for k, v in obj.items():
                    find_articles_debug(v)
            elif isinstance(obj, list):
                for item in obj:
                    find_articles_debug(item)

        find_articles_debug(data)
    else:
        print("No JSON found.")

except Exception as e:
    print(f"Error: {e}")
