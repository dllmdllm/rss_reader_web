
import asyncio
import logging
import sys
from rss_core.fetcher import AsyncFetcher
from rss_core.parser import HK01Parser

# Setup
feed_cache = {}
image_cache = {}
fetcher = AsyncFetcher(feed_cache, image_cache)
parser = HK01Parser(fetcher)

url = "https://www.hk01.com/社會新聞/60313636/渣馬-周潤發陪老友轉戰10公里-慢速完賽重享受-唔志在成"

async def test():
    print(f"Fetching {url}...")
    html_text = await fetcher.fetch_full_text(url)
    if not html_text:
        print("Failed to fetch HTML")
        return

    # Parse
    content, heroes, all_imgs = parser.parse(html_text, url)
    
    with open("debug_hk01_output.txt", "w", encoding="utf-8") as f:
        f.write(f"URL: {url}\n")
        f.write(f"Heroes: {heroes}\n")
        f.write(f"Total Imgs: {len(all_imgs)}\n")
        f.write("All Imgs List:\n")
        for i in all_imgs:
            f.write(f"- {i}\n")
        f.write("-" * 20 + "\n")
        f.write(content)
    
    print("Done. Wrote to debug_hk01_output.txt")
    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(test())
