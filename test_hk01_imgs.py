import asyncio
import json
import re
from rss_core.fetcher import AsyncFetcher
from rss_core.parser import HK01Parser

async def test_hk01_images():
    # Pass empty dicts for caches
    fetcher = AsyncFetcher(feed_cache={}, image_cache={})
    parser = HK01Parser(fetcher)
    
    test_urls = [
        "https://www.hk01.com/%E4%BA%BA%E6%B0%A3%E5%A8%9B%E6%A8%82/60313027/%E5%BC%B5%E5%B3%B0%E8%AD%B0%E5%9D%90%E7%9B%A34%E5%B9%B4-%E7%BE%9E%E6%8F%AD%E7%8D%84%E5%8F%8B%E8%BC%AA%E6%B5%81%E8%A7%A3%E6%B1%BA%E7%94%9F%E7%90%86%E9%9C%80%E8%A6%81-%E6%85%B6%E5%B9%B8%E4%B8%BB%E7%AE%A1%E5%BE%85%E4%BB%96%E4%B8%8D%E8%96%84",
        "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%A8%9B%E6%A8%82/60313563/ian%E9%99%B3%E5%8D%93%E8%B3%A2%E8%81%9E%E5%A7%9C%E6%BF%A4%E5%AF%86%E8%AC%80%E9%81%8E%E6%AA%94%E5%8D%B3%E6%89%AE%E5%82%BB-%E9%81%BF%E8%AB%87%E8%BD%89%E5%85%AC%E5%8F%B8%E5%82%B3%E8%81%9E-%E4%BD%A2%E5%8E%BB%E8%B8%A2%E8%8B%B1%E8%B6%85",
        "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%A8%9B%E6%A8%82/60313476/%E5%80%AA%E6%A8%82%E7%90%B3%E5%81%9A%E9%98%BF%E5%AF%B6%E5%8B%81%E8%88%88%E5%A5%AE-%E8%A2%AB%E8%91%89%E5%BF%B5%E7%90%9B%E7%99%BC%E6%8E%98-%E8%A9%B1%E5%94%94%E5%AE%9A%E4%BD%A2%E7%9D%87%E5%88%B0%E6%88%91%E6%B8%AF%E5%A5%B3%E5%98%85%E7%89%B9%E8%B3%AA",
        "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%A8%9B%E6%A8%82/60313562/%E5%AE%B9%E7%A5%96%E5%85%92%E7%9B%A4%E9%BB%9E%E8%94%A1%E5%8D%93%E5%A6%8D%E7%94%B7%E5%8F%8B%E4%BA%94%E5%A4%A7%E5%84%AA%E9%BB%9E-%E6%9F%93%E6%89%8B%E8%B6%B3%E5%8F%A3%E7%97%85%E6%9A%B4%E7%98%A68%E7%A3%85-%E9%A3%9F%E5%94%94%E5%88%B0%E5%98%A2",
        "https://www.hk01.com/%E7%9C%BE%E6%A8%82%E8%BF%B7/60313465/gin-lee%E6%8C%91%E6%88%B0%E4%B8%80%E9%8F%A1%E5%88%B0%E5%BA%95%E5%94%B1%E5%88%B0%E6%B5%B7%E6%B8%AF%E5%9F%8E-%E8%87%AA%E7%88%86%E6%9B%BE%E5%A3%93%E5%8A%9B%E5%A4%B1%E7%9C%A0%E5%AD%B8%E8%AD%98%E6%93%81%E6%8A%B1%E4%B8%8D%E5%AE%8C%E7%BE%8E",
        "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%A8%9B%E6%A8%82/60313565/%E4%B8%AD%E5%B9%B42-%E9%99%B3%E4%BF%9E%E9%9C%8F%E7%A2%A9%E5%A3%AB%E7%95%A2%E6%A5%AD%E8%AD%89%E6%9B%B8%E6%B7%98%E5%AF%B6%E8%B3%A355%E8%9A%8A-%E6%80%A5%E5%88%AApo-%E8%B2%BB%E4%BA%8B%E5%98%88%E4%BA%82%E5%B7%B4%E9%96%89",
        "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%A8%9B%E6%A8%82/60313554/%E6%98%8E%E6%98%9F%E9%81%8B%E5%8B%95%E6%9C%83-%E9%BB%83%E5%BA%AD%E9%8B%AD400%E7%B1%B3%E7%94%A9%E9%96%8B%E5%B0%8D%E6%89%8B%E5%A5%AA%E9%87%91-%E5%91%BD%E4%B8%AD%E8%A8%BB%E5%AE%9A%E5%BE%97%E7%AC%AC%E4%B8%80-%E6%9B%BE%E6%98%AF%E7%94%B0%E5%BE%91%E9%9A%8A%E4%BB%A3%E8%A1%A8",
        "https://www.hk01.com/%E5%8D%B3%E6%99%82%E5%A8%9B%E6%A8%82/60313210/%E5%91%A8%E6%BD%A4%E7%99%BC%E8%B7%91%E6%AD%A5%E5%8F%88%E8%A2%AB%E6%8D%95%E7%8D%B2-%E8%BD%B0%E5%8B%95%E5%B8%82%E6%B0%91%E5%9C%8D%E8%A7%80-%E7%99%BC%E5%93%A51%E5%80%8B%E8%A8%AD%E8%A8%88%E8%B7%91%E9%9E%8B%E6%88%90%E5%85%A8%E5%A0%B4%E7%84%A6%E9%BB%9E"
    ]
    
    for url in test_urls:
        print(f"\nProcessing: {url}")
        html = await fetcher.fetch_full_text(url)
        if not html:
            print("Failed to fetch")
            continue
            
        content, main_img, all_imgs = parser.parse(html, url)
        
        is_json = "hk01-image" in content
        print(f"Extraction Method: {'JSON' if is_json else 'HTML Fallback'}")
        print(f"Main Image: {main_img}")
        print(f"Image Count: {len(all_imgs)}")
        
        for i, img in enumerate(all_imgs[:3]):
            print(f"  Img {i+1}: {img}")
        if len(all_imgs) > 3:
            print(f"  ... and {len(all_imgs)-3} more")

    await fetcher.close()

if __name__ == "__main__":
    asyncio.run(test_hk01_images())
