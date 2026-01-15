
import os
import time
import json
import base64
import hashlib
import ssl
import threading
import asyncio
import urllib.request
import urllib.error
from urllib.parse import urlparse
from typing import Optional

import httpx

from .config import HTTP_TIMEOUT, IMAGES_DIR, IMAGE_CACHE_TTL, DEFAULT_USER_AGENT
from .utils import normalize_image_url

class Fetcher:
    """Original Thread-based Fetcher (Keeping for compatibility)"""
    def __init__(self, feed_cache: dict, image_cache: dict):
        self.feed_cache = feed_cache
        self.image_cache = image_cache
        self.feed_lock = threading.Lock()
        self.img_lock = threading.Lock()
        
        # Shared SSL context for CNBeta and others requiring lenient SSL
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def fetch_url(self, url: str) -> tuple[bytes, dict]:
        """
        Fetches a URL with ETag/Last-Modified caching.
        Returns (payload_bytes, meta_dict).
        """
        # Read from cache first
        with self.feed_lock:
            entry = self.feed_cache.get(url, {})
            
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        
        # 9to5Mac specialized headers to avoid 403
        if "9to5mac.com" in url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://9to5mac.com/",
            }

        # Cache headers
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]

        req = urllib.request.Request(url, headers=headers)
        
        try:
            # Use lenient SSL for specific domains, or default
            context = self.ssl_ctx if "rss.cnbeta.com.tw" in url else None
            
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=context) as resp:
                payload = resp.read()
                meta = {
                    "etag": resp.headers.get("ETag") or "",
                    "last_modified": resp.headers.get("Last-Modified") or "",
                    "timestamp": time.time(),
                }
                
                # Update cache on success
                with self.feed_lock:
                    cached_entry = {
                        "payload_b64": base64.b64encode(payload).decode("ascii"),
                        **meta
                    }
                    self.feed_cache[url] = cached_entry
                    
                return payload, meta

        except urllib.error.HTTPError as exc:
            if exc.code == 304 and entry.get("payload_b64"):
                return base64.b64decode(entry["payload_b64"]), entry
            
            # If server error but we have cache, fallback to cache
            if entry.get("payload_b64"):
                return base64.b64decode(entry["payload_b64"]), entry
            
            # Otherwise return empty
            return b"", {}
            
        except Exception:
            # Network error, fallback to cache
            if entry.get("payload_b64"):
                return base64.b64decode(entry["payload_b64"]), entry
            return b"", {}

    def fetch_full_text(self, url: str) -> str:
        """Simple fetch for HTML content (full text extraction)."""
        try:
            # Basic safe fetch
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def download_image(self, url: str, referer: str | None = None) -> str:
        """
        Downloads image, saves to images/ dir, returns local filename.
        Checks V2 cache first.
        """
        if not url: 
            return ""
            
        normalized_key = url.split("#")[0].split("?")[0] # Simple key normalization
        now = time.time()
        
        with self.img_lock:
            entry = self.image_cache.get(normalized_key, {})
            
        cached_path = entry.get("path", "")
        cached_ts = float(entry.get("timestamp", 0) or 0)
        
        # Return cached if valid
        if cached_path and (now - cached_ts) <= IMAGE_CACHE_TTL:
            full_path = os.path.join(IMAGES_DIR, cached_path)
            if os.path.exists(full_path):
                return cached_path
        
        # Download logic
        # 1. Determine referer
        site_ref = ""
        p = urlparse(url)
        if "pk.on.cc" in url or "on.cc" in url:
            site_ref = "https://hk.on.cc/"
        elif "mingpao.com" in url:
            site_ref = "https://news.mingpao.com/"
        elif "hk01.com" in url:
            site_ref = "https://www.hk01.com/"
        
        final_referer = referer or site_ref or f"{p.scheme}://{p.netloc}/"
        
        headers = {
            "User-Agent": "Mozilla/5.0", 
            "Accept": "image/*",
            "Referer": final_referer
        }
        
        if "cnbeta.com.tw" in url:
            headers["Origin"] = "https://www.cnbeta.com.tw"

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                
            if not data:
                return ""
                
            # Generate filename
            ext = ".jpg"
            if data.startswith(b"\x89PNG"): ext = ".png"
            elif data.startswith(b"GIF"): ext = ".gif"
            elif b"WEBP" in data[:16]: ext = ".webp"
            
            hash_name = hashlib.sha1(normalized_key.encode("utf-8")).hexdigest()[:16]
            filename = f"{hash_name}{ext}"
            save_path = os.path.join(IMAGES_DIR, filename)
            
            os.makedirs(IMAGES_DIR, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
                
            # Update cache
            with self.img_lock:
                self.image_cache[normalized_key] = {
                    "path": filename,
                    "timestamp": now,
                    "url": url
                }
                
            return filename
            
        except Exception as e:
            # print(f"Image fail {url}: {e}")
            return ""

class AsyncFetcher:
    """New Asyncio-based Fetcher using httpx"""
    def __init__(self, feed_cache: dict, image_cache: dict):
        self.feed_cache = feed_cache
        self.image_cache = image_cache
        # In async, we use the fact that dict mutations are atomic in GIL 
        # but better to avoid concurrent writes to same key if needed.
        # httpx client will be managed externally or here.
        self.client = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            verify=False, # Lenient SSL by default for CNBeta etc.
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT}
        )

    async def close(self):
        await self.client.aclose()

    async def fetch_url(self, url: str) -> tuple[bytes, dict]:
        entry = self.feed_cache.get(url, {})
        headers = {}
        
        if "9to5mac.com" in url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                "Referer": "https://9to5mac.com/",
            }
        
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]

        try:
            resp = await self.client.get(url, headers=headers)
            
            if resp.status_code == 304:
                if entry.get("payload_b64"):
                    return base64.b64decode(entry["payload_b64"]), entry
            
            resp.raise_for_status()
            payload = resp.content
            meta = {
                "etag": resp.headers.get("ETag") or "",
                "last_modified": resp.headers.get("Last-Modified") or "",
                "timestamp": time.time(),
            }
            
            self.feed_cache[url] = {
                "payload_b64": base64.b64encode(payload).decode("ascii"),
                **meta
            }
            return payload, meta

        except Exception as e:
            # Fallback to cache on any error
            if entry.get("payload_b64"):
                return base64.b64decode(entry["payload_b64"]), entry
            return b"", {}

    async def fetch_full_text(self, url: str) -> str:
        try:
            resp = await self.client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            return resp.text
        except Exception:
            return ""

    async def download_image(self, url: str, referer: str | None = None) -> str:
        if not url: return ""
        normalized_key = url.split("#")[0].split("?")[0]
        now = time.time()
        
        entry = self.image_cache.get(normalized_key, {})
        if entry.get("path") and (now - entry.get("timestamp", 0)) <= IMAGE_CACHE_TTL:
            full_path = os.path.join(IMAGES_DIR, entry["path"])
            if os.path.exists(full_path):
                return entry["path"]

        # Download
        site_ref = ""
        if "on.cc" in url: site_ref = "https://hk.on.cc/"
        elif "mingpao.com" in url: site_ref = "https://news.mingpao.com/"
        elif "hk01.com" in url: site_ref = "https://www.hk01.com/"
        
        final_referer = referer or site_ref or f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "image/*", "Referer": final_referer}
        
        try:
            resp = await self.client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.content
            
            ext = ".jpg"
            if data.startswith(b"\x89PNG"): ext = ".png"
            elif data.startswith(b"GIF"): ext = ".gif"
            elif b"WEBP" in data[:16]: ext = ".webp"
            
            hash_name = hashlib.sha1(normalized_key.encode("utf-8")).hexdigest()[:16]
            filename = f"{hash_name}{ext}"
            save_path = os.path.join(IMAGES_DIR, filename)
            
            os.makedirs(IMAGES_DIR, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
                
            self.image_cache[normalized_key] = {"path": filename, "timestamp": now, "url": url}
            return filename
        except Exception:
            return ""
