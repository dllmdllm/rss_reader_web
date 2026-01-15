import re
import html
import json
try:
    from lxml import html as LXML_HTML
except ImportError:
    LXML_HTML = None

from .utils import normalize_image_url, is_generic_image

def parse_stheadline_galleries(raw: str) -> dict:
    if not raw:
        return {}
    match = re.search(r"article_galleries\s*=\s*(\{.*?\});", raw, re.S)
    if not match:
        return {}
    data = match.group(1)
    try:
        return json.loads(data)
    except Exception:
        data = re.sub(r",\s*}", "}", data)
        data = re.sub(r",\s*]", "]", data)
        try:
            return json.loads(data)
        except Exception:
            return {}

def flatten_stheadline_gallery_urls(galleries: dict) -> list[str]:
    urls: list[str] = []
    if not galleries:
        return urls
    for _, images in galleries.items():
        if not isinstance(images, list):
            continue
        for item in images:
            if not isinstance(item, dict):
                continue
            src = item.get("src") or ""
            if not src:
                srcset = item.get("srcset") or ""
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            if src:
                urls.append(src)
    return urls

def inject_stheadline_galleries(fragment: str, galleries: dict) -> str:
    if not fragment or not galleries:
        return fragment
    if "<gallery-" not in fragment:
        return fragment

    def build_gallery_html(images: list[dict]) -> str:
        parts: list[str] = []
        for item in images:
            src = item.get("src") or ""
            if not src:
                srcset = item.get("srcset") or ""
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            if not src:
                continue
            alt = item.get("caption") or item.get("alt_text") or ""
            parts.append(
                f'<img src="{html.escape(src)}" alt="{html.escape(alt)}" loading="lazy" decoding="async">'
            )
        if not parts:
            return ""
        return "<div class=\"st-gallery\">" + "".join(parts) + "</div>"

    if LXML_HTML is None:
        new_fragment = fragment
        for key, images in galleries.items():
            html_block = build_gallery_html(images if isinstance(images, list) else [])
            if not html_block:
                html_block = ""
            new_fragment = re.sub(
                rf"<{re.escape(key)}[^>]*></{re.escape(key)}>",
                html_block,
                new_fragment,
                flags=re.IGNORECASE,
            )
            new_fragment = re.sub(
                rf"<{re.escape(key)}[^>]*/>",
                html_block,
                new_fragment,
                flags=re.IGNORECASE,
            )
        return new_fragment
    return fragment # TODO: LXML impl if needed, but regex is usually sufficient for these gallery tags

def clean_html_fragment(
    fragment: str,
    base_url: str,
    fetcher=None,
    download_images: bool = True,
) -> str:
    if not fragment:
        return ""
    if LXML_HTML is None:
        return fragment
    try:
        root = LXML_HTML.fragment_fromstring(fragment, create_parent="div")
        for node in root.xpath(".//script | .//style | .//noscript | .//video | .//iframe | .//button"):
            node.getparent().remove(node)
        if "stheadline.com" in base_url:
            for node in root.xpath(".//ad"):
                node.getparent().remove(node)
        else:
            for node in root.xpath(".//ad | .//*[starts-with(name(),'gallery-')]"):
                node.getparent().remove(node)
        
        if "rthk.hk" in base_url:
            # Remove "Share Tools" and social icons
            for node in root.xpath(".//*[contains(text(),'分享工具') or contains(text(),'列印') or contains(@class,'facebook') or contains(@class,'twitter')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            
            # Remove date string like "2026-01-14 HKT 11:15"
            for node in root.xpath(".//div | .//p | .//span"):
                text = (node.text_content() or "").strip()
                if re.search(r"\d{4}-\d{2}-\d{2}\s+HKT\s+\d{2}:\d{2}", text):
                    # If the node only contains the date, remove it
                    if len(text) < 30: 
                        parent = node.getparent()
                        if parent is not None:
                            parent.remove(node)
            
            # Attempt to remove duplicate title at the start
            # We assume the first significant text block might be the title
            # This is a heuristic: if the first paragraph is shortish and doesn't end with punctuation/is not a sentence, it might be a title.
            # But safer: RTHK often puts title in a specific structure or bold.
            pass

        if "mingpao.com" not in base_url:
            for node in root.xpath(
                ".//*[contains(@class,'related') or contains(@class,'keyword') "
                "or contains(@class,'share') or contains(@class,'social') "
                "or contains(@class,'breadcrumb')]"
            ):
                node.getparent().remove(node)
        else:
            for node in root.xpath(".//*[contains(text(),'相關字詞') or contains(text(),'報道詳情') or contains(text(),'編輯推介') or contains(text(),'熱門HOTPICK')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            # remove hyperlink-only blocks in Mingpao content
            for link in root.xpath(".//a[@href]"):
                parent = link.getparent()
                if parent is None:
                    continue
                imgs = link.xpath(".//img")
                text = (link.text_content() or "").strip()
                if imgs and not text:
                    # unwrap image links to keep images
                    for img in imgs:
                        parent.insert(parent.index(link), img)
                    parent.remove(link)
                    continue
                # remove linked text blocks entirely
                parent.remove(link)
            for node in root.xpath(".//*[contains(text(),'其他報道')]"):
                parent = node.getparent()
                if parent is not None and parent.getparent() is not None:
                    parent.getparent().remove(parent)
                # remove following link-only blocks
                for sib in list(parent.itersiblings()) if parent is not None else []:
                    text = (sib.text_content() or "").strip()
                    has_link = bool(sib.xpath(".//a[@href]"))
                    non_link_text = text
                    if has_link:
                        for a in sib.xpath(".//a"):
                            href = (a.get("href") or "").strip()
                            link_text = (a.text_content() or "").strip()
                            if link_text:
                                non_link_text = non_link_text.replace(link_text, "").strip()
                            if "finance.mingpao.com/fin/instantf" in href:
                                non_link_text = non_link_text.replace(href, "").strip()
                    if not text or (has_link and not non_link_text):
                        sib.getparent().remove(sib)
                        continue
                    break
        if "unwire.hk" in base_url:
            for link in root.xpath(".//a[@href]"):
                parent = link.getparent()
                if parent is None:
                    continue
                imgs = link.xpath(".//img")
                text = (link.text_content() or "").strip()
                # keep images, drop hyperlink wrapper
                if imgs:
                    for img in imgs:
                        parent.insert(parent.index(link), img)
                    if text:
                        parent.insert(parent.index(link), LXML_HTML.fromstring(f"<span>{html.escape(text)}</span>"))
                    parent.remove(link)
                    continue
                # replace link with plain text (no hyperlink)
                if text:
                    parent.insert(parent.index(link), LXML_HTML.fromstring(f"<span>{html.escape(text)}</span>"))
                parent.remove(link)
        if "stheadline.com" in base_url:
            # convert gallery links to img when href points to hkhl image
            for link in root.xpath(".//a[@href]"):
                href = link.get("href") or ""
                if "image.hkhl.hk" in href and not link.xpath(".//img"):
                    try:
                        img = LXML_HTML.Element("img")
                        img.set("src", href)
                        caption = link.get("data-caption") or ""
                        if caption:
                            img.set("alt", caption)
                        link.append(img)
                    except Exception:
                        pass
            for node in root.xpath(".//*[contains(text(),'同場加映') or contains(text(),'星島頭條App') or contains(text(),'即睇減息部署') or contains(text(),'立即下載') or contains(text(),'相關閱讀') or contains(text(),'相關閲讀') or contains(text(),'延伸閱讀')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'來源網址')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'相關新聞') or contains(text(),'相關閱讀') or contains(text(),'延伸閱讀')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'相關文章')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'最Hit')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(@class,'article-title')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(@class,'time') or contains(text(),'更新時間') or contains(text(),'發佈時間')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'下載') and contains(text(),'App')]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(".//*[contains(text(),'上車驗樓') or (contains(text(),'Email') and (contains(text(),'驗樓') or contains(text(),'新盤') or contains(text(),'裝修')))]"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(
                ".//*[contains(@class,'hit-articles') or contains(@class,'hit-block') or contains(@class,'hit-img') or contains(@class,'related') or contains(@class,'recommend')]"
            ):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for node in root.xpath(
                ".//articleflag | .//*[starts-with(@id,'videoplayer_')] | .//*[starts-with(@id,'videospan_')]"
            ):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
            for link in root.xpath(".//a"):
                # keep images, but remove text links
                if link.xpath(".//img"):
                    link.drop_tag()
                    continue
                parent = link.getparent()
                if parent is not None and parent.tag in ("p", "li", "div"):
                    parent.remove(link)
                    if (parent.text_content() or "").strip() == "":
                        gp = parent.getparent()
                        if gp is not None:
                            gp.remove(parent)
                else:
                    link.drop_tag()
        for img in root.xpath(".//img"):
            src = img.get("src")
            if src:
                placeholder = src.strip().lower()
                if any(k in placeholder for k in ("grey.gif", "blank.gif", "placeholder", "transparent.gif")):
                    src = ""
                elif is_generic_image(src, base_url):
                    src = ""
            if not src:
                srcset = img.get("srcset") or img.get("data-srcset")
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            if not src:
                src = img.get("data-src") or img.get("data-original")
            if src:
                normalized = normalize_image_url(base_url, src)
                img.set("src", normalized)
        if "stheadline.com" in base_url:
            for img in root.xpath(".//img[@src]"):
                src = img.get("src") or ""
                if "sthlstatic.com/sthl/assets/icons" in src or "sthlstatic.com/sthl/assets/images/logo" in src:
                    img.drop_tag()
        if fetcher and download_images:
            for img in root.xpath(".//img[@src]"):
                src = img.get("src")
                if not src:
                    continue
                # Download using fetcher
                local_name = fetcher.download_image(src, referer=base_url)
                if local_name:
                    img.set("src", f"images/{local_name}")
                img.set("loading", "lazy")
                img.set("decoding", "async")
        # cnbeta: remove duplicate paragraphs (normalized)
        if "cnbeta.com.tw" in base_url:
            seen_para: set[str] = set()
            for p in list(root.xpath(".//p")):
                raw_txt = (p.text_content() or "").strip()
                if not raw_txt:
                    continue
                norm = re.sub(r"[\W_]+", "", raw_txt).lower()
                if not norm:
                    continue
                if norm in seen_para:
                    parent = p.getparent()
                    if parent is not None:
                        parent.remove(p)
                    continue
                seen_para.add(norm)
        if "stheadline.com" in base_url:
            imgs = list(root.xpath(".//img"))
            seen_src: set[str] = set()
            for img in imgs:
                src = img.get("src") or ""
                if not src:
                    continue
                norm = re.sub(r"/f/\d+p0/0x0/[^/]+/", "/", src)
                norm = norm.split("?")[0].split("/")[-1]
                if norm in seen_src:
                    img.drop_tag()
                    continue
                seen_src.add(norm)
        for link in root.xpath(".//a[@href]"):
            href = normalize_image_url(base_url, link.get("href"))
            if not re.match(r"^https?://", href):
                link.drop_tag()
                continue
            link.set("href", href)
            link.set("target", "_blank")
            link.set("rel", "noopener")
        if "hk01.com" in base_url:
            for link in root.xpath(".//a"):
                link.drop_tag()
            for br in root.xpath(".//br"):
                br.drop_tag()
            for node in root.xpath(".//p | .//div | .//section | .//span"):
                if node.xpath(".//img"):
                    continue
                if (node.text_content() or "").strip() == "":
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)

        # CNBeta specific tail cleaning
        if "cnbeta" in base_url.lower():
            # Remove "Related News", "Topic", "Source" blocks (Regex & Text mapping)
            patterns = [
               "相关文章", "相關文章", "访问:", "訪問:", "來源：", "来源：", 
               "话题：", "話題：", "更多：", "更多:", "分享到：", "分享到:",
               "相关连结", "相關連結"
            ]
            for node in root.xpath(".//*"):
                txt = (node.text_content() or "").strip()
                if any(p in txt for p in patterns) and len(txt) < 100:
                    try:
                        node.drop_tree()
                    except Exception:
                        pass
            
            # Remove images that look like "Recommended" thumbnails or ads
            for img in root.xpath(".//img"):
                src = (img.get("src") or "").lower()
                if any(k in src for k in ["recommend", "thumb", "logo", "icon", "ads", "avatar"]):
                    try:
                        img.drop_tree()
                    except Exception:
                        pass

            # Remove ALL hyperlinks within content, keep only text
            for a in root.xpath(".//a"):
                text = (a.text_content() or "").strip()
                parent = a.getparent()
                if parent is not None:
                    if text:
                        span = LXML_HTML.Element("span")
                        span.text = text
                        parent.replace(a, span)
                    else:
                        parent.remove(a)
        
        # Final whitespace pruning for all
        for empty in root.xpath(".//*[not(node()) and not(self::img) and not(self::br)]"):
            try:
                empty.drop_tree()
            except Exception:
                pass
                    
        return LXML_HTML.tostring(root, encoding="unicode")
    except Exception:
        return fragment
