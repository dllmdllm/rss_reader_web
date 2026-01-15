
import re
import html
import json
import time
from typing import Any
from urllib.parse import urljoin
import lxml.html
from lxml import etree
import opencc

from .model import Item
from .utils import strip_html, normalize_image_url, is_generic_image, normalize_link

# Global converter
_CONVERTER = None
def get_converter():
    global _CONVERTER
    if _CONVERTER is None:
        try:
            # Use 's2hk' for Hong Kong Traditional Chinese (User Request)
            _CONVERTER = opencc.OpenCC('s2hk')
        except Exception:
            try:
                _CONVERTER = opencc.OpenCC('s2hk.json')
            except Exception:
                _CONVERTER = None
    return _CONVERTER

def to_trad(text: str) -> str:
    c = get_converter()
    return c.convert(text) if c and text else text

# --- XPath Definitions ---
XPATHS = {
    "og_image": ["//meta[@property='og:image']/@content"],
    "twitter_image": ["//meta[@name='twitter:image']/@content"],
    
    # RTHK
    "rthk_images": [
       "//div[contains(@class,'itemImage')]//img/@src",
       "//div[contains(@class,'videoDisplay')]//img/@src",
       "//div[contains(@class,'img-slide-wrap')]//img/@src",
    ],
    "rthk_fulltext": ["//div[contains(@class,'itemFullText')]"],
    
    # Mingpao
    "mingpao_images": [
        "//div[contains(@class,'fancybox-buttons')]//img/@href",
        "//a[contains(@class,'fancybox-buttons')]/@href",
        "//div[contains(@class,'gallery_wrapper')]//img/@src",
        "//figure//img/@src",
    ],
    "mingpao_fulltext": [
        "//div[@id='blockcontent']//article[contains(@class,'txt4')]",
        "//article[contains(@class,'txt4')]",
        "//div[@id='upper']", 
        "//div[contains(@class,'article_content')]",
    ],
    
    # Singtao / Stheadline
    "stheadline_images": [
        "//a[contains(@class,'gallery-item')]/@href", 
        "//div[contains(@class,'std-slider')]//img/@src",
        "//figure//img/@src"
    ],
    "stheadline_fullhtml": [
        "//div[contains(@class,'article-content')]",
        "//div[contains(@class,'main-body')]",
        "//div[contains(@class,'content-main')]",
    ],
    
    # The Witness
    "thewitness_images": [
        "//div[contains(@class,'entry-content')]//img/@src",
        "//figure//img/@src"
    ],
    "thewitness_fullhtml": [
        "//div[contains(@class,'entry-content')]",
        "//article",
    ],

    # HKEPC
    "hkepc_fullhtml": [
        "//div[@id='view']", 
        "//div[contains(@class,'article-content')]"
    ],

    # Unwire
    "unwire_fullhtml": [
        "//div[contains(@class,'entry-content')]",
        "//div[contains(@class,'post-content')]"
    ],
    
    # CNBeta
    "cnbeta_images": [
        "//div[@id='article_content']//img/@src",
        "//article//img/@src"
    ],
    "cnbeta_fullhtml": [
         "//div[@id='article_content']",
         "//div[contains(@class,'article-content')]"
    ],
    
    # Generic
    "generic_fullhtml": ["//article", "//div[contains(@class,'content')]"],
}

def xpath_union(paths: list[str]) -> str:
    return " | ".join(paths)


class BaseParser:
    """Base class for all site parsers."""
    
    def __init__(self, fetcher):
        self.fetcher = fetcher

    def parse(self, html_content: str, url: str) -> tuple[str, str, list[str]]:
        """
        Returns (content_html, main_image_url, list_of_all_images)
        """
        if not html_content:
            return "", "", []
            
        try:
            root = lxml.html.fromstring(html_content)
        except Exception:
            return "", "", []
            
        # 1. Extract Main Image (OG/Twitter)
        main_img = ""
        og = root.xpath(xpath_union(XPATHS["og_image"]))
        if og: main_img = og[0].strip()
        
        if not main_img:
            tw = root.xpath(xpath_union(XPATHS["twitter_image"]))
            if tw: main_img = tw[0].strip()

        # 2. Extract Content & Specific Images
        content_html, extra_imgs = self._extract_content(root, url)
        
        # Filter Bad Images from list
        def is_bad_img(u):
            u = u.lower()
            return "waiting.gif" in u or "prev.png" in u or "next.png" in u or "loading" in u or "spinner" in u

        extra_imgs = [i for i in extra_imgs if not is_bad_img(i)]
        
        if not main_img and extra_imgs:
            main_img = extra_imgs[0]
            
        return content_html, main_img, extra_imgs

    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        """Override this in subclasses."""
        # Default implementation, try generic
        nodes = root.xpath(xpath_union(XPATHS["generic_fullhtml"]))
        if not nodes:
            return "", []
            
        node = nodes[0]
        html_str = lxml.html.tostring(node, encoding="unicode")
        imgs = node.xpath(".//img/@src")
        return html_str, imgs

    def clean_html(self, html_fragment: str, base_url: str) -> str:
        """Heavily cleans extraction result."""
        if not html_fragment: return ""
        try:
            root = lxml.html.fragment_fromstring(html_fragment, create_parent="div")
            
            # Remove bad tags
            for tag in root.xpath(".//script | .//style | .//noscript | .//iframe | .//button | .//ad | .//video"):
                tag.drop_tree()
                
            # Remove inline styles (fixes spinning animations driven by style="")
            for el in root.xpath(".//*[@style]"):
                del el.attrib["style"]

            # Remove loading indicators and interactive placeholders
            for node in root.xpath(".//*[contains(@class,'loading') or contains(@class,'spinner') or contains(@class,'videoHolder') or contains(@class,'audioPlayer')]"):
                node.drop_tree()
                
            # Remove empty links or social share
            for node in root.xpath(".//*[contains(@class,'share') or contains(@class,'social') or contains(@class,'related')]"):
                node.drop_tree()

            # Fix Images
            for img in root.xpath(".//img"):
                src = img.get("src") or img.get("data-src") or img.get("data-original")
                if src and "loading" not in src.lower() and "spinner" not in src.lower() and "waiting.gif" not in src.lower() and "prev.png" not in src.lower() and "next.png" not in src.lower():
                    img.set("src", normalize_image_url(base_url, src))
                    # Remove loading/srcset to avoid browser confusion in static file
                    if img.get("srcset"): del img.attrib["srcset"]
                    if img.get("loading"): del img.attrib["loading"]
                else:
                    img.drop_tag()

            # Fix Links (open in new tab)
            for a in root.xpath(".//a"):
                a.set("target", "_blank")
                
            return lxml.html.tostring(root, encoding="unicode")
        except Exception:
            return html_fragment


class RTHKParser(BaseParser):
    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        nodes = root.xpath(xpath_union(XPATHS["rthk_fulltext"]))
        if not nodes: return "", []
        
        # RTHK images often outside fulltext, in header
        imgs = root.xpath(xpath_union(XPATHS["rthk_images"]))
        
        html_str = lxml.html.tostring(nodes[0], encoding="unicode")
        
        # Inject Hero Image at Top (User Request: Follow visual order)
        if imgs:
            hero_html = f'<figure class="rthk-hero"><img src="{imgs[0]}" style="width:100%; height:auto; display:block; margin-bottom:10px;"/></figure>'
            html_str = hero_html + html_str
            
        return html_str, imgs

    def parse(self, html_content: str, url: str) -> tuple[str, str, list[str]]:
        c, m, i = super().parse(html_content, url)
        
        # Regex Fallback for RTHK Images (Slider/Hidden)
        # Find ALL Large images (L.jpg) to capture full gallery
        import re
        matches = re.findall(r'https?://newsstatic\.rthk\.hk/images/mfile_\d+_\d+_[L]\.jpg', html_content)
        
        if not matches:
             matches = re.findall(r'https?://newsstatic\.rthk\.hk/images/mfile_\d+_\d+_[A-Z]+\.jpg', html_content)
             
        if matches:
             # Deduplicate and Filter existing
             unique_new_imgs = []
             seen = set(i) # Existing images from xpath
             
             for img_url in matches:
                 if img_url not in seen:
                     unique_new_imgs.append(img_url)
                     seen.add(img_url)
             
             # If we found new images via regex
             if unique_new_imgs:
                 # 1. Update List
                 i.extend(unique_new_imgs)
                 
                 # 2. Update Main Image if missing
                 if not m: 
                     m = unique_new_imgs[0]
                     
                 # 3. Inject ALL new images into content
                 # (User wants to see ALL matches, e.g. the full gallery)
                 gallery_html = ""
                 for img_url in unique_new_imgs:
                     # Check to avoid inserting if already present in text (safe check)
                     if img_url not in c:
                         gallery_html += f'<figure class="rthk-item"><img src="{img_url}" style="width:100%; height:auto; display:block; margin: 10px 0;"/></figure>'
                 
                 if gallery_html:
                     # Prepend to content (Gallery usually at top)
                     # Note: unique_new_imgs only has images NOT in xpath, so duplication is minimized.
                     c = gallery_html + c
        
        return c, m, i


class MingPaoParser(BaseParser):
    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        nodes = root.xpath(xpath_union(XPATHS["mingpao_fulltext"]))
        if not nodes: return "", []
        
        # Cleaning specific to Mingpao
        for node in nodes[0].xpath(".//*[contains(text(),'相關字詞') or contains(text(),'報道詳情')]"):
             node.drop_tree()
             
        html_str = lxml.html.tostring(nodes[0], encoding="unicode")
        imgs = root.xpath(xpath_union(XPATHS["mingpao_images"]))
        return html_str, imgs


class SingtaoParser(BaseParser):
    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        nodes = root.xpath(xpath_union(XPATHS["stheadline_fullhtml"]))
        if not nodes: return "", []
        
        node = nodes[0]
        # Remove "Extension Reading"
        for bad in node.xpath(".//*[contains(text(),'延伸閱讀') or contains(text(),'相關新聞')]"):
             bad.getparent().remove(bad)
             
        # Extract gallery images (data-fancybox)
        imgs = root.xpath(xpath_union(XPATHS["stheadline_images"]))
        
        html_str = lxml.html.tostring(node, encoding="unicode")
        return html_str, imgs


class CNBetaParser(BaseParser):
    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        nodes = root.xpath(xpath_union(XPATHS["cnbeta_fullhtml"]))
        if not nodes: return "", []
        
        html_str = lxml.html.tostring(nodes[0], encoding="unicode")
        # Convert to Traditional Chinese
        html_str = to_trad(html_str)
        
        imgs = root.xpath(xpath_union(XPATHS["cnbeta_images"]))
        return html_str, imgs
        

class HK01Parser(BaseParser):
    """HK01 uses hidden JSON in the HTML usually."""
    def parse(self, html_content: str, url: str) -> tuple[str, str, list[str]]:
        # Try Regex extract JSON block if standard parse fails
        # But for now, let's look for standard article body which HK01 also renders
        # Or better: rely on your original logic which parsed the API response directly in fetcher?
        # Actually in V1, you fetched list via API, but content via HTML.
        # Let's stick to HTML scraping for content.
        
        try:
            root = lxml.html.fromstring(html_content)
            
            # HK01 images
            imgs = []
            # They put images in figure tags
            for img in root.xpath("//article//img/@src"):
                imgs.append(img)
                
            # Content
            articles = root.xpath("//article")
            if articles:
                 # Remove "Extension"
                 for bad in articles[0].xpath(".//*[contains(@class, '延伸閱讀')]"):
                     bad.drop_tree()
                 html_str = lxml.html.tostring(articles[0], encoding="unicode")
                 return html_str, (imgs[0] if imgs else ""), imgs
                 
        except Exception:
            pass
            
        return "", "", []


class NineToFiveMacParser(BaseParser):
    def parse(self, html_content: str, url: str) -> tuple[str, str, list[str]]:
        c, m, i = super().parse(html_content, url)
        # Translation placeholder - to be implemented properly if needed
        # For now, just return English
        return c, m, i

# --- Factory ---

def get_parser(url: str, fetcher) -> BaseParser:
    if "rthk.hk" in url: return RTHKParser(fetcher)
    if "mingpao.com" in url: return MingPaoParser(fetcher)
    if "stheadline.com" in url: return SingtaoParser(fetcher)
    if "cnbeta" in url: return CNBetaParser(fetcher)
    if "hk01.com" in url: return HK01Parser(fetcher)
    if "9to5mac.com" in url: return NineToFiveMacParser(fetcher)
    return BaseParser(fetcher)
