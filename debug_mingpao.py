
import asyncio
from rss_core.fetcher import AsyncFetcher

async def main():
    fetcher = AsyncFetcher({}, {})
    url = "https://news.mingpao.com/rss/ins/s00001.xml" 
    
    print(f"Fetching MingPao: {url}")
    content, meta = await fetcher.fetch_url(url)
    
    if content:
        print(f"Success! Content length: {len(content)}")
        try:
            txt = content.decode('utf-8')
            print(f"Head: {txt[:200]}")
        except:
             try:
                 txt = content.decode('big5')
                 print(f"Head (Big5): {txt[:200]}")
             except:
                 print("Decode failed.")
    else:
        print("Failed to fetch MingPao.")

    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(main())
