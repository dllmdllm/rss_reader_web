
import os

# Crawler / Fetcher Settings
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HTTP_TIMEOUT = 18
SINGTAO_TIMEOUT = 12

# Concurrency
DEFAULT_THREADS = 10

# Caching
DATA_DIR = os.path.join(os.getcwd(), "data")
IMAGES_DIR = os.path.join(os.getcwd(), "images")

FEED_CACHE_PATH = os.path.join(DATA_DIR, "feed_cache.json")
IMAGE_CACHE_PATH = os.path.join(DATA_DIR, "image_cache.json")
FULLTEXT_CACHE_PATH = os.path.join(DATA_DIR, "fulltext_cache.json")
FULLHTML_CACHE_PATH = os.path.join(DATA_DIR, "fullhtml_cache.json")
TRANSLATE_CACHE_PATH = os.path.join(DATA_DIR, "translate_cache.json")

FULLTEXT_CACHE_TTL = 6 * 60 * 60
IMAGE_CACHE_TTL = 24 * 60 * 60
FULLHTML_CACHE_TTL = 6 * 60 * 60
CACHE_GC_TTL = 7 * 24 * 60 * 60

# Logic
DEFAULT_LOOKBACK_HOURS = 6.0
DEFAULT_REFRESH_SECONDS = 600
DEFAULT_MAX_ITEMS = 200

MIXED_MODE = True

# Limits
CNBETA_LIMIT = 20
THEWITNESS_LIMIT = 20
HK01_LIMIT = 20
ONCC_LIMIT = 30
SINGTAO_ENT_LIMIT = 20
SITE_DIR = os.getcwd()
CACHE_DIR = DATA_DIR
