
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
from .html_cleaner import clean_html_fragment

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
        "//div[contains(@class,'itemImage')]//img/@data-src",
        "//div[contains(@class,'itemImage')]//img/@data-original",
        "//div[contains(@class,'videoDisplay')]//img/@src",
        "//div[contains(@class,'videoDisplay')]//img/@data-src",
        "//div[contains(@class,'videoDisplay')]//img/@data-original",
        "//div[contains(@class,'img-slide-wrap')]//img/@src",
        "//div[contains(@class,'img-slide-wrap')]//img/@data-src",
        "//div[contains(@class,'img-slide-wrap')]//img/@data-original"
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
        
        if main_img and is_bad_img(main_img):
            main_img = ""

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

    async def clean_html(self, html_fragment: str, base_url: str, main_img: str = None) -> str:
        """Heavily cleans extraction result using consolidated cleaner."""
        if not html_fragment: return ""
        return await clean_html_fragment(html_fragment, base_url, fetcher=self.fetcher, hero_img=main_img)


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
        
        # Find ALL Large images (L.jpg) to capture full gallery
        import re
        matches = re.findall(r'https?://newsstatic\.rthk\.hk/images/mfile_\d+_\d+_[Ll]\.jpg', html_content)
        
        if not matches:
             matches = re.findall(r'https?://newsstatic\.rthk\.hk/images/mfile_\d+_\d+_[A-Za-z]+\.jpg', html_content)
             
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
        
        content_node = nodes[0]
        
        # 1. Cleaning: Remove unwanted Mingpao elements
        for node in content_node.xpath(".//*[contains(text(),'相關字詞') or contains(text(),'報道詳情')]"):
             node.drop_tree()
             
        # 2. Intelligent Image Recovery
        # Mingpao often links thumbnails to high-res images using 'fancybox' class or just <a> links
        # We want to replace the thumbnail <img> with the high-res <img>
        
        # Find all fancybox links in the root (Full page context)
        # Because sometimes the gallery sequence is in the header, not body
        gallery_imgs = []
        for a in root.xpath("//a[contains(@class,'fancybox') or contains(@class,'fancybox-buttons')]"):
            href = a.get('href')
            if href and (href.endswith('.jpg') or href.endswith('.png')):
                gallery_imgs.append(href)
                
        # Also check standard gallery wrapper
        for img in root.xpath("//div[contains(@class,'gallery_wrapper')]//img"):
             src = img.get('src')
             if src: gallery_imgs.append(src)
             
        # Dedupe
        gallery_imgs = list(dict.fromkeys(gallery_imgs))
        
        # Strategy: 
        # A. Walk through content_node. If we find an <a> linking to an image, unwrap it to an <img>.
        # B. If we find an <img> that is a thumbnail (contains /thumbnail/ or similar), try to match with high-res.
        # C. If images are NOT in the text at all, prepend them (Gallery style).
        
        content_imgs = content_node.xpath(".//img/@src")
        has_inline_images = len(content_imgs) > 0
        
        # Transform links to images
        for a in content_node.xpath(".//a"):
            href = a.get('href')
            if href and (href.endswith('.jpg') or href.endswith('.png')):
                # It's a link to an image. Replace <a>Text</a> with <img src="href">
                # Check if it already contains an img
                if not a.xpath(".//img"):
                    new_img = lxml.html.Element("img", src=href)
                    new_img.set("class", "recovered-img")
                    a.getparent().replace(a, new_img)
                    has_inline_images = True
                    
        html_str = lxml.html.tostring(content_node, encoding="unicode")
        
        # If we found gallery images but the content seems void of them (or very few), 
        # it's likely a "Top Gallery + Text" layout. Prepend the gallery.
        # But we must avoid duplicates.
        if gallery_imgs and not has_inline_images:
            gallery_html = ""
            for img_url in gallery_imgs:
                gallery_html += f'<figure class="mingpao-gallery"><img src="{img_url}" style="width:100%; display:block;"/></figure>'
            html_str = gallery_html + html_str
            
        # Return all found images for caching
        all_imgs = gallery_imgs + content_imgs
        return html_str, all_imgs


class SingtaoParser(BaseParser):
    def parse(self, html_content: str, url: str) -> tuple[str, str, list[str]]:
        # Need to capture raw html for Regex parsing of JS variables BEFORE standard parsing
        self.raw_html = html_content
        return super().parse(html_content, url)

    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        nodes = root.xpath(xpath_union(XPATHS["stheadline_fullhtml"]))
        if not nodes: return "", []
        
        node = nodes[0]
        # Remove "Extension Reading"
        for bad in node.xpath(".//*[contains(text(),'延伸閱讀') or contains(text(),'相關新聞')]"):
             bad.getparent().remove(bad)
        
        # --- Intelligent Gallery Injection ---
        # Stheadline uses <gallery-id> tags in content and a separate JS mapping
        # 1. Parse the JSON map
        import re
        import json
        
        galleries_map = {}
        # Look for: article_galleries = {"gallery-123": [...], ...};
        match = re.search(r"article_galleries\s*=\s*(\{.*?\});", self.raw_html, re.S)
        if match:
            try:
                # Need to handle loose JSON if necessary, but typically it's valid JS object
                # It might not be strict JSON (keys not quoted).
                # Simplified approach: If it fails, rely on raw cleaning steps? 
                # Better: clean the JS string to valid JSON or use a robust parser if available.
                # For now let's try a simple loose parse or just basic pattern matching if needed.
                # But actually, html_cleaner.py has `parse_stheadline_galleries` helper we can borrow logic from,
                # but better to reimplement simple version here to modify the tree directly.
                
                json_str = match.group(1)
                # Quick fix for common JS obj quirks if strictly needed, but let's try mostly direct load 
                # or finding gallery keys in the string.
                # Actually, let's just find the mapping in the simplest way:
                # We know the keys are like "gallery-12345".
                # We can iterate the keys found in the Content Node.
                pass
            except: pass

        # 2. Find placeholder tags in Content
        # They look like: <gallery-1065706 class="gallery-widget" ...></gallery-1065706>
        
        # We need to extract the ID from the tag name
        # lxml creates Elements with tag names like "gallery-1065706"
        
        all_imgs = []
        
        # Iterate all children to find gallery tags - Collect first to avoid modification issues during iteration
        gallery_tags = []
        for element in node.xpath(".//*"):
            if isinstance(element.tag, str) and element.tag.startswith("gallery-"):
                gallery_tags.append(element)

        for element in gallery_tags:
            gallery_key = element.tag
            # Now try to find this key in raw_html
            # Pattern: "gallery-12345": [ { ... "src": "..." } ]
            # We use regex to find the array for this specific key
            g_regex = rf'"{gallery_key}"\s*:\s*(\[.*?\])'
            g_match = re.search(g_regex, self.raw_html, re.S)
            
            replacement_done = False
            if g_match:
                try:
                    import ast
                    # It's usually JS objects, so ast.literal_eval might fail if keys aren't quoted.
                    # But STHeadline usually puts keys in quotes in that JSON blob.
                    # If strict JSON fails, we can try to just regex extracts URLs.
                    g_data_str = g_match.group(1)
                    
                    # Extract all "src": "URL"
                    img_urls = re.findall(r'"src"\s*:\s*"([^"]+)"', g_data_str)
                    
                    # Clean escaped slashes: https:\/\/ -> https://
                    img_urls = [u.replace(r'\/', '/') for u in img_urls]
                    
                    if img_urls:
                        # Create a new structure: <div class="st-gallery"> <img...> <img...> </div>
                        new_div = lxml.html.Element("div", **{"class": "st-gallery-injected"})
                        for i_url in img_urls:
                            if i_url.startswith("//"): i_url = "https:" + i_url
                            img_el = lxml.html.Element("img", src=i_url)
                            img_el.set("class", "st-gallery-img")
                            img_el.set("style", "width:100%; height:auto; margin-bottom:8px;")
                            new_div.append(img_el)
                            all_imgs.append(i_url)
                            
                        # Replace the <gallery-xxx> tag with this new div
                        parent = element.getparent()
                        if parent is not None:
                            parent.replace(element, new_div)
                            replacement_done = True
                except Exception as e:
                    # print(f"Singtao Gallery Error: {e}")
                    pass
            
            # If replacement parsing failed, remove the ugly tag anyway
            if not replacement_done:
                 try:
                     element.drop_tree()
                 except: pass

        # 3. Fallback: If no galleries found but slider exists
        slider_imgs = root.xpath("//div[contains(@class,'std-slider')]//img/@src")
        if slider_imgs:
             # Only add unique images
             new_slider_imgs = [s for s in slider_imgs if s not in all_imgs]
             all_imgs.extend(new_slider_imgs)
             
             # If content is short/empty, or no images in content, prepend slider
             if not node.xpath(".//img"):
                 # Prepend string injection (modify node is harder to prepend raw string)
                 # Easy way: insert elements
                 for s_img in reversed(new_slider_imgs):
                      new_img = lxml.html.Element("img", src=s_img)
                      node.insert(0, new_img)

        html_str = lxml.html.tostring(node, encoding="unicode")
        
        # Supplement regex-extracted images
        # The BaseParser will add these to the extraction list if we return them
        return html_str, all_imgs


class CNBetaParser(BaseParser):
    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        nodes = root.xpath(xpath_union(XPATHS["cnbeta_fullhtml"]))
        if not nodes: return "", []
        
        # NOTE: We NO LONGER inject a hero image here because the template handles it separately,
        # and it often leads to duplicates if the image is already in the article body.
        html_str = lxml.html.tostring(nodes[0], encoding="unicode")
        imgs = root.xpath(xpath_union(XPATHS["cnbeta_images"]))
        return html_str, imgs

    def parse(self, html_content: str, url: str) -> tuple[str, str, list[str]]:
        # 1. Base extraction
        c, m, i = super().parse(html_content, url)
        
        # 2. Regex Scan for ALL CDN images (Dual Grabbing)
        import re
        matches = re.findall(r'https?://static\.cnbetacdn\.com/[^\s"\'>]+\.(?:jpg|png|gif|jpeg)', html_content)
        
        if matches:
            unique_new_imgs = []
            seen = set()
            # Normalize existing images from XPath to avoid duplicates
            for existing in i:
                fname = existing.split('/')[-1].split('?')[0].lower()
                seen.add(fname)
            
            for img_url in matches:
                fname = img_url.split('/')[-1].split('?')[0].lower()
                if fname not in seen and not "icon" in img_url.lower():
                    unique_new_imgs.append(img_url)
                    seen.add(fname)
            
            if unique_new_imgs:
                i.extend(unique_new_imgs)
                if not m: m = unique_new_imgs[0]
                
                gallery_html = ""
                for img_url in unique_new_imgs:
                    # Final safety check before injection
                    fname = img_url.split('/')[-1].split('?')[0].lower()
                    bad = any(k in img_url.lower() for k in ["icon", "thumb", "recommend", "logo", "avatar", "ads"])
                    if fname not in c.lower() and not bad:
                        gallery_html += f'<figure class="cnbeta-item"><img src="{img_url}" style="width:100%; height:auto; display:block; margin: 10px 0;"/></figure>'
                
                if gallery_html:
                    c = c + gallery_html
        
        # 3. Final Conversion to Traditional Chinese
        c = to_trad(c)
        
        return c, m, i
        

class HK01Parser(BaseParser):
    """HK01 uses Next.js, so we try to extract from __NEXT_DATA__ for perfect ordering."""
    
    def parse(self, html_content: str, url: str) -> tuple[str, str, list[str]]:
        # 1. Try __NEXT_DATA__ JSON Extraction (Gold Standard for Order)
        try:
            import json
            import re
            
            # Find the JSON blob
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_content, re.S)
            if match:
                data = json.loads(match.group(1))
                
                # Navigate standard HK01 Next.js structure
                # props -> pageProps -> article -> blocks
                # Note: The path might vary slightly, so we look for 'article' safely
                
                article = None
                try:
                    props = data.get('props', {}).get('pageProps', {})
                    # Direct article
                    if 'article' in props:
                        article = props['article']
                    # Sometimes provided as 'initialState'
                    elif 'initialState' in props:
                        # deeper search provided if key exists
                        pass 
                except: pass
                
                # If explicit path failed, search for 'blocks' in the whole blob? Too risky.
                # Let's stick to 'props.pageProps.article' which is standard for article pages.
                
                if article and 'blocks' in article:
                    html_parts = []
                    all_imgs = []
                    
                    for block in article['blocks']:
                        b_type = block.get('type')
                        b_data = block.get('data', {})
                        
                        if b_type == 'text':
                            # Text block
                            txt = b_data.get('text', '')
                            if txt:
                                html_parts.append(f'<p>{txt}</p>')
                                
                        elif b_type == 'image':
                            # Image block
                            # URL usually in 'url' or 'originalUrl'
                            img_url = b_data.get('originalUrl') or b_data.get('url')
                            caption = b_data.get('caption', '')
                            
                            if img_url:
                                all_imgs.append(img_url)
                                fig = f'<figure class="hk01-image"><img src="{img_url}" style="width:100%; display:block;"/><figcaption>{caption}</figcaption></figure>'
                                html_parts.append(fig)
                                
                        elif b_type == 'header':
                            # Subheader
                            txt = b_data.get('text', '')
                            if txt:
                                html_parts.append(f'<h3>{txt}</h3>')

                    if html_parts:
                        content_html = "".join(html_parts)
                        main_img = all_imgs[0] if all_imgs else ""
                        return content_html, main_img, all_imgs

        except Exception as e:
            # print(f"HK01 JSON parse failed: {e}")
            pass
            
        # 2. Fallback to HTML Parsing (Improved)
        try:
            root = lxml.html.fromstring(html_content)
            
            # Remove "Extension Reading" / "Related"
            for bad in root.xpath(".//*[contains(@class, '延伸閱讀')]"):
                bad.drop_tree()
            
            articles = root.xpath("//article")
            if articles:
                 article_node = articles[0]
                 
                 # Fix Lazy Loading Images: data-src -> src
                 # HK01 uses data-src usually
                 all_imgs = []
                 for img in article_node.xpath(".//img"):
                     real_src = img.get('data-src') or img.get('src')
                     if real_src:
                         img.set('src', real_src)
                         all_imgs.append(real_src)
                         
                 html_str = lxml.html.tostring(article_node, encoding="unicode")
                 return html_str, (all_imgs[0] if all_imgs else ""), all_imgs
                 
        except Exception:
            pass
            
        return "", "", []


class OnCCParser(BaseParser):
    def _extract_content(self, root, url) -> tuple[str, list[str]]:
        # On.cc structure: .breakingNewsContent is usually the body container
        nodes = root.xpath("//div[contains(@class,'breakingNewsContent')] | //div[contains(@class,'news-section')]")
        if not nodes: 
            # Fallback for some sub-domains
            nodes = root.xpath("//div[@id='content']//div[@class='content']")
            
        if not nodes: return "", []
        
        node = nodes[0]
        html_str = ""
        all_imgs = []
        
        # 1. Image Extraction (High Res)
        # On.cc often uses a top gallery or single large photo at the top
        # Structure: <div class="photo"> <a href="LARGE_IMG" title="..."> <img src="THUMB"...> </a> </div>
        
        top_gallery_html = ""
        # Find all gallery items in the article context
        gallery_items = root.xpath("//div[contains(@class,'photo')]//a[contains(@class,'fancybox')] | //div[contains(@class,'photo')]//a[@href]")
        
        seen_imgs = set()
        
        for a in gallery_items:
            href = a.get('href')
            if href and (href.endswith('.jpg') or href.endswith('.png')):
                if href not in seen_imgs:
                    seen_imgs.add(href)
                    all_imgs.append(href)
                    caption = a.get('title') or ""
                    # Create a nice figure
                    top_gallery_html += f'<figure class="oncc-gallery"><img src="{href}" style="width:100%; display:block;"/><figcaption>{caption}</figcaption></figure>'

        # 2. Content Cleaning
        # Remove related news, useless buttons
        for bad in node.xpath(".//*[contains(@class,'related')] | .//*[contains(@class,'bottom_link')] | .//*[contains(@class,'fb_iframe')]"):
            bad.drop_tree()
            
        # 3. Inline images? 
        # Sometimes on.cc puts images inline. Let's capture them too.
        for img in node.xpath(".//img"):
            src = img.get('src')
            if src and src not in seen_imgs:
                seen_imgs.add(src)
                all_imgs.append(src)
        
        content_html = lxml.html.tostring(node, encoding="unicode")
        
        # 4. Construct Final HTML
        # If we found top gallery images, put them FIRST (standard layout for On.cc)
        # Unless the content already contains them (unlikely for the 'breakingNewsContent' div which is usually text only)
        
        html_str = top_gallery_html + content_html
        
        return html_str, all_imgs


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
    if "on.cc" in url: return OnCCParser(fetcher)
    if "9to5mac.com" in url: return NineToFiveMacParser(fetcher)
    return BaseParser(fetcher)
