#!/usr/bin/env python3
"""
Pharma Scraper Pro — Core Engine
================================
All business logic for scraping, canonicalisation, FOS enrichment,
price analysis, and export.  Designed to be driven by a Tkinter GUI.

Modules:
  - scrape      : multi-site Shopify + HTML scraping
  - canonical   : dedupe by barcode + fuzzy name matching
  - enrich      : FOS stock report barcode/name matching
  - price       : competitor price analysis with size-mismatch detection
  - export      : multi-sheet Excel (Canonical, Source, Price, Shopify, eBay)
  - crossdomain : copy barcodes across domains via fuzzy matching
"""

from __future__ import annotations

import os, sys, re, time, json, html, sqlite3, logging, hashlib, threading, queue
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from difflib import SequenceMatcher
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
import pandas as pd
from bs4 import BeautifulSoup

# ── PATHS ──────────────────────────────────────────────────────────
ENGINE_DIR = Path(__file__).parent.resolve()
ROOT_DIR   = ENGINE_DIR.parent
DATA_DIR   = ROOT_DIR / "data"
EXPORT_DIR = ROOT_DIR / "exports"
REPORT_DIR = ROOT_DIR / "reports"
LOG_DIR    = ROOT_DIR / "logs"
DB_PATH    = DATA_DIR / "canonical_products.db"
CONFIG_PATH = ROOT_DIR / "config.json"

for d in (DATA_DIR, EXPORT_DIR, REPORT_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── LOGGER ───────────────────────────────────────────────────────
logger = logging.getLogger("pharma_pro")

# ── DEFAULT SITE LIST (50 Australian pharmacy/supplement sites) ──
DEFAULT_SITES: List[Dict[str, Any]] = [
    {"rank":1,  "name":"Pharmacy Online",           "domain":"pharmacyonline.com.au",         "enabled":True, "type":"Online pharmacy",           "difficulty":"Low"},
    {"rank":2,  "name":"Chemist Direct",            "domain":"chemistdirect.com.au",          "enabled":True, "type":"Discount online pharmacy",  "difficulty":"Low"},
    {"rank":3,  "name":"Pharmacy Direct",           "domain":"pharmacydirect.com.au",         "enabled":True, "type":"Online pharmacy",           "difficulty":"Low"},
    {"rank":4,  "name":"Better Value Pharmacy",     "domain":"bettervaluepharmacy.com.au",    "enabled":True, "type":"Discount pharmacy",         "difficulty":"Low"},
    {"rank":5,  "name":"Pharmacy 4 Less",           "domain":"pharmacy4less.com.au",          "enabled":True, "type":"Discount chemist chain",    "difficulty":"Low"},
    {"rank":6,  "name":"Aussie Health Products",  "domain":"aussiehealthproducts.com.au",   "enabled":True, "type":"Health food / supplements","difficulty":"Low"},
    {"rank":7,  "name":"Megavitamins",              "domain":"megavitamins.com.au",           "enabled":True, "type":"Vitamins / supplements",    "difficulty":"Low"},
    {"rank":8,  "name":"Zenith Pharmacy",           "domain":"zenithpharmacy.com.au",         "enabled":True, "type":"Online pharmacy",           "difficulty":"Low-Medium"},
    {"rank":9,  "name":"Michael's Chemist",         "domain":"michaelschemist.com.au",        "enabled":True, "type":"Family pharmacy",           "difficulty":"Low-Medium"},
    {"rank":10, "name":"National Custom Compounding","domain":"customcompounding.com.au",   "enabled":True, "type":"Compounding pharmacy",      "difficulty":"Medium"},
    {"rank":11, "name":"Healthylife",               "domain":"healthylife.com.au",            "enabled":True, "type":"Online health retailer",    "difficulty":"Medium"},
    {"rank":12, "name":"Chemist2U",                 "domain":"chemist2u.com.au",              "enabled":True, "type":"Online pharmacy platform",  "difficulty":"Medium"},
    {"rank":13, "name":"Compounding Pharmacy AU",  "domain":"compoundingaustralia.com.au",  "enabled":True, "type":"Compounding pharmacy",      "difficulty":"Medium"},
    {"rank":14, "name":"TerryWhite Chemmart",       "domain":"terrywhitechemmart.com.au",     "enabled":True, "type":"Major pharmacy chain",      "difficulty":"Medium"},
    {"rank":15, "name":"Direct Chemist Outlet",     "domain":"directchemistoutlet.com.au",    "enabled":True, "type":"Discount chemist",          "difficulty":"Medium"},
    {"rank":16, "name":"Chemist Warehouse",         "domain":"chemistwarehouse.com.au",       "enabled":True, "type":"Major pharmacy chain",      "difficulty":"Medium-High"},
    {"rank":17, "name":"Chemist Works",             "domain":"chemistworks.com.au",           "enabled":True, "type":"Online chemist",            "difficulty":"Medium"},
    {"rank":18, "name":"Mr Supplement",             "domain":"mrsupplement.com.au",           "enabled":True, "type":"Sports supplements",        "difficulty":"Low"},
    {"rank":19, "name":"ePharmacy",                 "domain":"epharmacy.com.au",              "enabled":True, "type":"Online pharmacy",           "difficulty":"Medium"},
    {"rank":20, "name":"Complete Health",           "domain":"completehealth.com.au",         "enabled":True, "type":"Supplements / health",      "difficulty":"Low-Medium"},
    {"rank":21, "name":"Evelyn Faye Nutrition",     "domain":"evelynfaye.com.au",             "enabled":True, "type":"Supplements / health",      "difficulty":"Low-Medium"},
    {"rank":22, "name":"Family Pharmacy Granville", "domain":"familypharmacy.com.au",         "enabled":True, "type":"Family/community pharmacy", "difficulty":"Medium"},
    {"rank":23, "name":"Discount Drug Stores",      "domain":"discountdrugstores.com.au",     "enabled":True, "type":"Pharmacy chain",            "difficulty":"Medium"},
    {"rank":24, "name":"Amcal",                     "domain":"amcal.com.au",                  "enabled":True, "type":"Pharmacy chain",            "difficulty":"Medium"},
    {"rank":25, "name":"My Chemist",                "domain":"mychemist.com.au",              "enabled":True, "type":"Discount chemist",          "difficulty":"Medium"},
    {"rank":26, "name":"Super Pharmacy",            "domain":"superpharmacy.com.au",          "enabled":True, "type":"Online pharmacy",           "difficulty":"Low-Medium"},
    {"rank":27, "name":"Simple Online Pharmacy",    "domain":"simpleonlinepharmacy.com.au",   "enabled":True, "type":"Online pharmacy",           "difficulty":"Low-Medium"},
    {"rank":28, "name":"InstantScripts Shop",       "domain":"instantscripts.com.au",         "enabled":True, "type":"Online pharmacy / telehealth","difficulty":"Medium-High"},
    {"rank":29, "name":"The Compounding Pharmacy",  "domain":"thecompoundingpharmacy.com.au", "enabled":True, "type":"Compounding pharmacy",      "difficulty":"Medium"},
    {"rank":30, "name":"Kingsway Compounding",      "domain":"kingswaycompounding.com.au",    "enabled":True, "type":"Compounding pharmacy",      "difficulty":"Medium"},
    {"rank":31, "name":"My Compounding Pharmacy",   "domain":"mycompounding.com.au",          "enabled":True, "type":"Compounding pharmacy",      "difficulty":"Medium"},
    {"rank":32, "name":"National Pharmacies",       "domain":"nationalpharmacies.com.au",     "enabled":True, "type":"Pharmacy chain",            "difficulty":"Medium"},
    {"rank":33, "name":"Blooms The Chemist",        "domain":"bloomsthechemist.com.au",       "enabled":True, "type":"Pharmacy chain",            "difficulty":"Medium"},
    {"rank":34, "name":"Priceline Pharmacy",        "domain":"priceline.com.au",              "enabled":True, "type":"Pharmacy / beauty chain", "difficulty":"Medium"},
    {"rank":35, "name":"Soul Pattinson Chemist",    "domain":"soulpattinsonchemist.com.au",   "enabled":True, "type":"Pharmacy chain",            "difficulty":"Medium"},
    {"rank":36, "name":"SuperPharmacyPlus",         "domain":"superpharmacyplus.com.au",      "enabled":True, "type":"Online pharmacy",           "difficulty":"Low-Medium"},
    {"rank":37, "name":"Your Discount Chemist",     "domain":"yourdiscountchemist.com.au",    "enabled":True, "type":"Discount pharmacy",         "difficulty":"Low-Medium"},
    {"rank":38, "name":"Doctors Own",               "domain":"doctorsown.com.au",             "enabled":True, "type":"Practitioner/health retailer","difficulty":"Medium"},
    {"rank":39, "name":"Go Vita",                   "domain":"govita.com.au",                 "enabled":True, "type":"Health food / supplements", "difficulty":"Low-Medium"},
    {"rank":40, "name":"A Vitamin Place",           "domain":"avitaminplace.com.au",          "enabled":True, "type":"Vitamins / supplements",    "difficulty":"Low"},
    {"rank":41, "name":"Australian Vitamins",       "domain":"australianvitamins.com",        "enabled":True, "type":"Vitamins / supplements",    "difficulty":"Low"},
    {"rank":42, "name":"The Healthy Place",         "domain":"thehealthyplace.com.au",        "enabled":True, "type":"Supplements / natural health","difficulty":"Low-Medium"},
    {"rank":43, "name":"Elite Supps",               "domain":"elitesupps.com.au",             "enabled":True, "type":"Sports supplements",        "difficulty":"Low-Medium"},
    {"rank":44, "name":"Bulk Nutrients",            "domain":"bulknutrients.com.au",          "enabled":True, "type":"Supplements",               "difficulty":"Low"},
    {"rank":45, "name":"Amino Z",                   "domain":"aminoz.com.au",                 "enabled":True, "type":"Sports supplements",        "difficulty":"Low"},
    {"rank":46, "name":"Mass Nutrition",            "domain":"massnutrition.com.au",          "enabled":True, "type":"Sports supplements",        "difficulty":"Low-Medium"},
    {"rank":47, "name":"ASN Online",                "domain":"australiansportsnutrition.com.au","enabled":True,"type":"Sports supplements",      "difficulty":"Low-Medium"},
    {"rank":48, "name":"Fit Supplements",           "domain":"fitsupplements.com.au",         "enabled":True, "type":"Supplements",               "difficulty":"Low"},
    {"rank":49, "name":"Nutrition Capital",         "domain":"nutritioncapital.com.au",       "enabled":True, "type":"Supplements",               "difficulty":"Low"},
    {"rank":50, "name":"Vitamin Grocer AU",         "domain":"au.vitamingrocer.com",          "enabled":True, "type":"Vitamins / supplements",    "difficulty":"Low-Medium"},
]

# ── SQL ──────────────────────────────────────────────────────────
SQL_UPSERT = """
INSERT INTO source_products (
    scrape_batch, source_name, source_domain, product_name, brand, barcode, sku,
    pack_size, variant, category, subcategory, description, ingredients,
    image_url, product_url, current_price, sale_price, currency, stock_status,
    scraped_at, quality_flags, raw_json
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(source_domain, product_url) DO UPDATE SET
    scrape_batch=excluded.scrape_batch,
    source_name=excluded.source_name,
    product_name=excluded.product_name,
    brand=excluded.brand,
    barcode=CASE WHEN excluded.barcode IS NOT NULL AND excluded.barcode!='' THEN excluded.barcode ELSE source_products.barcode END,
    sku=CASE WHEN excluded.sku IS NOT NULL AND excluded.sku!='' THEN excluded.sku ELSE source_products.sku END,
    pack_size=excluded.pack_size,
    variant=excluded.variant,
    category=excluded.category,
    subcategory=excluded.subcategory,
    description=excluded.description,
    ingredients=excluded.ingredients,
    image_url=CASE WHEN excluded.image_url IS NOT NULL AND excluded.image_url!='' THEN excluded.image_url ELSE source_products.image_url END,
    current_price=excluded.current_price,
    sale_price=excluded.sale_price,
    currency=excluded.currency,
    stock_status=excluded.stock_status,
    scraped_at=excluded.scraped_at,
    quality_flags=TRIM(COALESCE(source_products.quality_flags,'')||';'||COALESCE(excluded.quality_flags,''),';'),
    raw_json=excluded.raw_json
"""

SHOPIFY_PROBES = [
    "https://{d}/products.json?limit=250&page=1",
    "https://www.{d}/products.json?limit=250&page=1",
    "https://{d}/collections/all/products.json?limit=250&page=1",
    "https://www.{d}/collections/all/products.json?limit=250&page=1",
]

# ═══════════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════════

def get_conn():
    """Acquire a connection from the thread-safe pool."""
    from db_utils import get_pooled_conn, set_pool
    global _pool_initialized
    try:
        _pool_initialized
    except NameError:
        _pool_initialized = False
    if not _pool_initialized:
        set_pool(DB_PATH, max_conn=4)
        _pool_initialized = True
    return get_pooled_conn()

def close_conn(conn):
    """Release a connection back to the pool."""
    from db_utils import release_conn
    release_conn(conn)

def _init_pool():
    from db_utils import set_pool, ensure_indexes
    set_pool(DB_PATH, max_conn=4)
    conn = get_conn()
    ensure_indexes(conn)
    close_conn(conn)
    global _pool_initialized
    _pool_initialized = True


def init_db() -> sqlite3.Connection:
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS source_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scrape_batch TEXT NOT NULL,
        source_name TEXT, source_domain TEXT NOT NULL,
        product_name TEXT, brand TEXT, barcode TEXT, sku TEXT,
        pack_size TEXT, variant TEXT, category TEXT, subcategory TEXT,
        description TEXT, ingredients TEXT, image_url TEXT, product_url TEXT,
        current_price REAL, sale_price REAL, currency TEXT DEFAULT 'AUD',
        stock_status TEXT, scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
        quality_flags TEXT, raw_json TEXT,
        UNIQUE(source_domain, product_url)
    );
    CREATE TABLE IF NOT EXISTS canonical_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_name TEXT, canonical_brand TEXT, canonical_barcode TEXT,
        canonical_size TEXT, canonical_category TEXT, canonical_subcategory TEXT,
        canonical_description TEXT, canonical_ingredients TEXT, canonical_image_url TEXT,
        match_type TEXT DEFAULT 'barcode', match_confidence REAL DEFAULT 1.0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        quality_flags TEXT,
        -- FOS columns
        fos_apn TEXT, fos_stock_name TEXT, fos_cost REAL, fos_avg_cost REAL,
        fos_sell_price REAL, fos_soh REAL, fos_margin_pct REAL,
        fos_categories TEXT, fos_dept TEXT, fos_qty_sold REAL, fos_sales_val REAL,
        fos_match_type TEXT, fos_match_confidence REAL
    );
    CREATE TABLE IF NOT EXISTS canonical_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_id INTEGER NOT NULL, source_product_id INTEGER NOT NULL,
        source_name TEXT, source_domain TEXT, source_price REAL,
        source_sale_price REAL, source_stock_status TEXT, source_product_url TEXT, scraped_at TEXT,
        FOREIGN KEY (canonical_id) REFERENCES canonical_products(id),
        FOREIGN KEY (source_product_id) REFERENCES source_products(id)
    );
    CREATE TABLE IF NOT EXISTS fos_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_id INTEGER, fos_apn TEXT, fos_stock_name TEXT, fos_full_name TEXT,
        match_type TEXT, match_confidence REAL, enriched_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(canonical_id, fos_apn)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cb ON canonical_products(canonical_barcode)
        WHERE canonical_barcode != '' AND canonical_barcode IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_sb ON source_products(barcode);
    CREATE INDEX IF NOT EXISTS idx_sd ON source_products(source_domain);
    CREATE INDEX IF NOT EXISTS idx_cs ON canonical_sources(canonical_id);
    """)
    conn.commit()
    return conn

# ═══════════════════════════════════════════════════════════════════
# ── LOGGER ───────────────────────────────────────────────────────
logger = logging.getLogger("pharma_pro")
logger.setLevel(logging.DEBUG)
_log_formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s")

# Console handler
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_log_formatter)
logger.addHandler(_console_handler)

# File handler (rotates daily-ish via timestamp in filename)
_log_file = LOG_DIR / f"scrape_{datetime.now():%Y%m%d}.log"
_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_log_formatter)
logger.addHandler(_file_handler)

def log(msg: str, level: str = "info"):
    ts = datetime.now().isoformat()
    line = f"{ts} | {msg}"
    print(line, flush=True)
    getattr(logger, level.lower(), logger.info)(msg)


from validators import BarcodeValidator, ProxyRotator, SiteErrorReporter, FuzzyMatcher, is_likely_js_rendered

def norm_barcode(val) -> str:
    """Normalize and validate a barcode using check-digit verification."""
    return BarcodeValidator.normalize(val)


def first_valid_barcode(*values) -> str:
    for v in values:
        bc = norm_barcode(v)
        if bc:
            return bc
    return ""


def norm_str(s: str) -> str:
    s = html.unescape(s or "").lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s.strip()


def extract_size(name: str) -> Tuple[str, str]:
    if not name:
        return "", name
    m = re.search(
        r"(\d+\s*(?:mg|ml|g|mcg|iu|tb|cap|tab|softgel|capsules|tablets|pack|bottle|jar|"
        r"vegan caps|v caps|vcaps|enteric coated))", name, re.I)
    if m:
        sz = m.group(1).strip().lower()
        base = re.sub(re.escape(m.group(0)), "", name, flags=re.I).strip()
        return sz, base
    return "", name


def sim(a: str, b: str) -> float:
    """High-performance fuzzy similarity using rapidfuzz."""
    return FuzzyMatcher.ratio(a, b)


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-AU,en;q=0.9",
    })
    return s


def to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


# ── Barcode extraction from text ─────────────────────────────────
BARCODE_LABEL_RE = re.compile(
    r"(?:ean|upc|gtin(?:-?8|-?12|-?13|-?14)?|barcode|bar\s*code|apn)"
    r"\s*(?:[:#=]|is|</?[^>]+>)*\s*([0-9][0-9\s\-]{6,18}[0-9])", re.I)
BARCODE_JSON_RE = re.compile(
    r'"(?:barcode|bar_code|gtin|gtin8|gtin12|gtin13|gtin14|upc|ean|apn)"\s*:\s*"?([0-9][0-9\s\-]{6,18}[0-9])"?', re.I)


def extract_barcodes_from_text(text: str) -> List[str]:
    if not text:
        return []
    found = []
    for rx in (BARCODE_JSON_RE, BARCODE_LABEL_RE):
        for m in rx.finditer(text):
            bc = norm_barcode(m.group(1))
            if bc and bc not in found:
                found.append(bc)
    return found


def flatten_jsonld(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from flatten_jsonld(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from flatten_jsonld(item)


def extract_product_page_identifiers(sess: requests.Session, product_url: str) -> Dict[str, Any]:
    result = {"page_barcode": "", "sku_to_barcode": {}, "title_to_barcode": {}, "raw_barcodes": []}
    try:
        r = sess.get(product_url, timeout=20, headers={"Accept": "text/html,*/*"})
        if r.status_code != 200:
            return result
        soup = BeautifulSoup(r.text, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or script.get_text(" "))
            except Exception:
                continue
            for node in flatten_jsonld(ld):
                vals = [node.get(k) for k in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "upc", "ean", "barcode", "mpn")]
                bc = first_valid_barcode(*vals)
                if bc:
                    result["raw_barcodes"].append(bc)
                    result["page_barcode"] = result["page_barcode"] or bc
                    sku = str(node.get("sku") or "").strip()
                    name = str(node.get("name") or "").strip().lower()
                    if sku:
                        result["sku_to_barcode"][sku] = bc
                    if name:
                        result["title_to_barcode"][name] = bc
        for bc in extract_barcodes_from_text(r.text):
            if bc not in result["raw_barcodes"]:
                result["raw_barcodes"].append(bc)
            result["page_barcode"] = result["page_barcode"] or bc
        for script in soup.find_all("script"):
            txt = script.string or script.get_text(" ")
            if not txt or ("barcode" not in txt.lower() and "gtin" not in txt.lower()):
                continue
            for m in re.finditer(r'\{[^{}]{0,1200}?(?:"sku"\s*:\s*"([^"]*)")[^{}]{0,1200}?\}', txt, re.I):
                blob = m.group(0)
                sku = (m.group(1) or "").strip()
                bc = first_valid_barcode(*extract_barcodes_from_text(blob))
                if sku and bc:
                    result["sku_to_barcode"][sku] = bc
    except Exception:
        pass
    return result


# ═══════════════════════════════════════════════════════════════════
#  SCRAPER
# ═══════════════════════════════════════════════════════════════════

def is_shopify_json(r: requests.Response) -> bool:
    try:
        d = r.json()
        return isinstance(d, dict) and "products" in d
    except Exception:
        return False


def probe_shopify(sess: requests.Session, domain: str) -> Optional[str]:
    for tmpl in SHOPIFY_PROBES:
        url = tmpl.format(d=domain)
        try:
            r = sess.get(url, timeout=15)
            if r.status_code == 200 and is_shopify_json(r):
                cnt = len(r.json().get("products", []))
                log(f"[{domain}] Shopify JSON @ {url} ({cnt} products)", "success")
                return url
            elif r.status_code == 200:
                log(f"[{domain}] 200 OK but not Shopify JSON — likely {r.headers.get('content-type','unknown')[:40]}", "warning")
            elif r.status_code == 429:
                log(f"[{domain}] rate limited (429)", "warning")
                time.sleep(3)
                continue
            elif r.status_code == 403:
                log(f"[{domain}] blocked (403)", "warning")
            else:
                log(f"[{domain}] HTTP {r.status_code}", "warning")
        except requests.exceptions.Timeout:
            log(f"[{domain}] timeout probing {url}", "warning")
        except Exception as e:
            log(f"[{domain}] error probing: {e}", "warning")
        time.sleep(0.2)
    log(f"[{domain}] No Shopify JSON endpoint found — skipping", "warning")
    return None


def scrape_shopify(sess: requests.Session, endpoint: str, domain: str, batch_label: str,
                   db_conn: sqlite3.Connection, msg_queue: Optional[queue.Queue] = None,
                   stop_event: Optional[threading.Event] = None) -> Tuple[int, int]:
    base = f"https://{domain}"
    page = 1
    total_prods = 0
    total_vars  = 0

    while True:
        if stop_event and stop_event.is_set():
            break
        url = endpoint.replace("page=1", f"page={page}")
        try:
            r = sess.get(url, timeout=25)
            if r.status_code in (429, 503):
                log(f"[{domain}] rate limited, backoff 3s")
                time.sleep(3)
                continue
            r.raise_for_status()
            prods = r.json().get("products", [])
            if not prods:
                break

            rows = []
            for p in prods:
                if stop_event and stop_event.is_set():
                    break
                variants = p.get("variants", [{}])
                images = p.get("images", [])
                img = images[0].get("src", "") if images else ""
                ptype = p.get("product_type", "")
                tags = ", ".join(p.get("tags", [])[:5])
                handle = p.get("handle", "")
                body = p.get("body_html", "")
                vendor = p.get("vendor", "")
                product_url = f"{base}/products/{handle}"
                needs_page_lookup = any(not norm_barcode(v.get("barcode", "")) for v in variants)
                page_ids = extract_product_page_identifiers(sess, product_url) if needs_page_lookup else {"page_barcode": "", "sku_to_barcode": {}, "title_to_barcode": {}, "raw_barcodes": []}

                for v in variants:
                    vt = v.get("title", "")
                    pname = f"{p.get('title', '')} - {vt}" if (vt and vt != "Default Title") else p.get("title", "")
                    sku = str(v.get("sku", "") or "").strip()
                    barcode = first_valid_barcode(
                        v.get("barcode", ""),
                        page_ids.get("sku_to_barcode", {}).get(sku, ""),
                        page_ids.get("title_to_barcode", {}).get(str(pname).lower(), ""),
                        page_ids.get("page_barcode", "") if len(variants) == 1 else "",
                        sku,
                    )
                    qflags = []
                    if barcode and barcode != norm_barcode(v.get("barcode", "")):
                        qflags.append("barcode_enriched_from_page_or_sku")
                    if not barcode:
                        qflags.append("missing_barcode")

                    variant_id = v.get("id") or sku or vt or "default"
                    variant_url = f"{product_url}?variant={variant_id}"
                    rows.append((
                        batch_label, vendor or domain.split(".")[0].title(), domain,
                        pname, vendor, barcode, sku, "", vt,
                        ptype or tags, "", body, "", img, variant_url,
                        float(v.get("price", 0) or 0),
                        float(v.get("compare_at_price", 0) or 0) if v.get("compare_at_price") else 0.0,
                        "AUD",
                        "available" if v.get("inventory_quantity", 0) > 0 else "out_of_stock",
                        datetime.now().isoformat(),
                        ";".join(qflags),
                        json.dumps({"product": p, "page_identifier_lookup": page_ids}),
                    ))

            total_prods += len(prods)
            total_vars  += len(rows)
            if rows:
                c = db_conn.cursor()
                try:
                    c.executemany(SQL_UPSERT, rows)
                    db_conn.commit()
                except Exception as e:
                    log(f"[{domain}] DB insert error: {e}")
            if msg_queue:
                msg_queue.put({"type": "site_progress", "domain": domain, "products": total_prods, "variants": total_vars})
            if len(prods) < 250:
                break
        except Exception as e:
            log(f"[{domain}] page {page} error: {e}")
            break
        page += 1
        time.sleep(0.5)

    log(f"[{domain}] DONE | {total_prods} products | {total_vars} variants")
    return total_prods, total_vars


def run_shopify_batch(targets: List[str], batch_label: str = "batch",
                      msg_queue: Optional[queue.Queue] = None,
                      stop_event: Optional[threading.Event] = None,
                      proxies: Optional[List[str]] = None) -> Dict[str, Any]:
    sess = get_session()
    rotator = ProxyRotator(proxies)
    reporter = SiteErrorReporter()
    if rotator.proxies:
        log(f"Proxy rotation enabled: {len(rotator.proxies)} proxies")
    conn = get_conn()
    grand_products = 0
    grand_variants = 0
    results = []

    for idx, domain in enumerate(targets, 1):
        if stop_event and stop_event.is_set():
            break
        log(f"\n({idx}/{len(targets)}) {domain}")
        if msg_queue:
            msg_queue.put({"type": "site_start", "idx": idx, "total": len(targets), "domain": domain})
        # Rotate proxy per site
        if rotator.proxies:
            rotator.rotate_for_session(sess)
            log(f"  Using proxy: {sess.proxies.get('http', 'none')}")
        try:
            ep = probe_shopify(sess, domain)
            if ep:
                p, v = scrape_shopify(sess, ep, domain, batch_label, conn, msg_queue, stop_event)
                grand_products += p
                grand_variants += v
                results.append({"site": domain, "status": "success", "products": p, "variants": v, "url": ep})
            else:
                # Try detecting JS-rendered site
                try:
                    r = sess.get(f"https://{domain}", timeout=10)
                except Exception:
                    r = None
                if r and is_likely_js_rendered(r.text):
                    reporter.log(domain, "js_rendered", "Site appears JS-rendered (Next.js/Nuxt/Vue/Angular detected)", {"status": r.status_code})
                    results.append({"site": domain, "status": "js_required", "products": 0, "variants": 0, "reason": "JS-rendered site — needs Selenium/Playwright"})
                else:
                    # HTML fallback for non-Shopify sites
                    log(f"[{domain}] Trying HTML fallback...", "info")
                    from html_fallback import probe_and_scrape_html
                    p, v = probe_and_scrape_html(sess, domain, batch_label, conn, msg_queue, stop_event)
                    if p > 0:
                        grand_products += p
                        grand_variants += v
                        results.append({"site": domain, "status": "html_fallback", "products": p, "variants": v, "reason": "Scraped via HTML fallback"})
                        log(f"[{domain}] HTML fallback: {p} products", "success")
                    else:
                        reporter.log(domain, "no_catalog", "No Shopify JSON and no products found via HTML fallback", {"status": r.status_code if r else "timeout"})
                        results.append({"site": domain, "status": "failed", "products": 0, "variants": 0, "reason": "No Shopify JSON; HTML fallback found nothing"})
        except Exception as e:
            reporter.log(domain, "exception", str(e), {})
            results.append({"site": domain, "status": "error", "products": 0, "variants": 0, "reason": str(e)})
        time.sleep(0.5)

    close_conn(conn)
    html_sites = sum(1 for r in results if r['status']=='html_fallback')
    js_sites = sum(1 for r in results if r['status']=='js_required')
    failed_sites = sum(1 for r in results if r['status']=='failed')
    log(f"\n{'='*60}\nBATCH SUMMARY\n  Products: {grand_products}\n  Variants: {grand_variants}\n  Shopify sites: {sum(1 for r in results if r['status']=='success')}\n  HTML fallback sites: {html_sites}\n  JS-rendered sites: {js_sites}\n  Failed sites: {failed_sites}\n{'='*60}")
    if reporter.errors:
        report_path = REPORT_DIR / f"error_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        reporter.export_json(str(report_path))
        log(f"Error report saved: {report_path}")
    return {"total_products": grand_products, "total_variants": grand_variants, "sites": results, "error_report": str(report_path) if reporter.errors else ""}


# ═══════════════════════════════════════════════════════════════════
#  CANONICALISATION
# ═══════════════════════════════════════════════════════════════════

def canonicalise(msg_queue: Optional[queue.Queue] = None,
                 stop_event: Optional[threading.Event] = None) -> Dict[str, Any]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Build existing canonical index
    c.execute("SELECT id, canonical_brand, canonical_size, canonical_name FROM canonical_products")
    idx = {}
    for row in c.fetchall():
        key = (norm_str(row["canonical_brand"]), norm_str(row["canonical_size"]))
        idx.setdefault(key, []).append((row["id"], norm_str(row["canonical_name"])))

    # Fetch uncanonicalised source rows
    c.execute("""
        SELECT * FROM source_products
        WHERE id NOT IN (SELECT source_product_id FROM canonical_sources)
    """)
    rows = c.fetchall()
    log(f"Canonicalising {len(rows)} new source products...")

    inserted = 0
    linked = 0
    fuzzy_linked = 0

    for src in rows:
        if stop_event and stop_event.is_set():
            break

        barcode = norm_barcode(src["barcode"])
        brand = (src["brand"] or "").strip()
        name = (src["product_name"] or "").strip()
        size, base_name = extract_size(name)
        norm_name = norm_str(base_name or name)
        key = (norm_str(brand), norm_str(size))

        # 1. Barcode match
        if barcode:
            c.execute("SELECT id FROM canonical_products WHERE canonical_barcode=?", (barcode,))
            canon = c.fetchone()
            if canon:
                c.execute("""
                    INSERT OR IGNORE INTO canonical_sources
                    (canonical_id, source_product_id, source_name, source_domain, source_price, source_sale_price,
                     source_stock_status, source_product_url, scraped_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (canon["id"], src["id"], src["source_name"], src["source_domain"],
                      src["current_price"], src["sale_price"], src["stock_status"], src["product_url"], src["scraped_at"]))
                linked += 1
                continue

        # 2. Fuzzy name match within same brand+size bucket
        candidates = idx.get(key, [])
        best_id = None
        best_score = 0.0
        for cid, cname in candidates:
            score = sim(norm_name, cname)
            if score > best_score:
                best_score = score
                best_id = cid

        if best_id and best_score >= 0.85:
            c.execute("""
                INSERT OR IGNORE INTO canonical_sources
                (canonical_id, source_product_id, source_name, source_domain, source_price, source_sale_price,
                 source_stock_status, source_product_url, scraped_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (best_id, src["id"], src["source_name"], src["source_domain"],
                  src["current_price"], src["sale_price"], src["stock_status"], src["product_url"], src["scraped_at"]))
            fuzzy_linked += 1
            continue

        # 3. Create new canonical
        c.execute("""
            INSERT INTO canonical_products
            (canonical_name, canonical_brand, canonical_barcode, canonical_size, canonical_category,
             canonical_description, canonical_image_url, match_type, match_confidence)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (name, brand, barcode, size, src["category"] or "",
              src["description"] or ""[:500], src["image_url"] or "",
              "barcode" if barcode else "fuzzy", 1.0 if barcode else round(best_score, 3)))
        new_id = c.lastrowid
        c.execute("""
            INSERT INTO canonical_sources
            (canonical_id, source_product_id, source_name, source_domain, source_price, source_sale_price,
             source_stock_status, source_product_url, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (new_id, src["id"], src["source_name"], src["source_domain"],
              src["current_price"], src["sale_price"], src["stock_status"], src["product_url"], src["scraped_at"]))
        idx.setdefault(key, []).append((new_id, norm_name))
        inserted += 1

        if (inserted + linked + fuzzy_linked) % 500 == 0:
            conn.commit()
            if msg_queue:
                msg_queue.put({"type": "canonical_progress", "inserted": inserted, "linked": linked, "fuzzy": fuzzy_linked})

    conn.commit()
    c.execute("SELECT COUNT(*) FROM canonical_products")
    total_canonical = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM canonical_sources")
    total_links = c.fetchone()[0]
    close_conn(conn)

    log(f"Canonicalisation complete: {inserted} new, {linked} barcode-linked, {fuzzy_linked} fuzzy-linked. Total canonicals: {total_canonical}")
    return {"inserted": inserted, "linked": linked, "fuzzy_linked": fuzzy_linked,
            "total_canonical": total_canonical, "total_links": total_links}


# ═══════════════════════════════════════════════════════════════════
#  FOS ENRICHMENT
# ═══════════════════════════════════════════════════════════════════

def enrich_fos(fos_path: str, msg_queue: Optional[queue.Queue] = None,
               stop_event: Optional[threading.Event] = None) -> Dict[str, Any]:
    from rapidfuzz import fuzz as rapidfuzz_fuzz

    fos_path = Path(fos_path)
    if not fos_path.exists():
        return {"error": f"FOS file not found: {fos_path}"}

    log(f"Reading FOS report from {fos_path}")
    fos_raw = pd.read_excel(fos_path, sheet_name='Stock Report')

    # ── FOS Column Guard ──────────────────────────────────────────
    from db_utils import FOSColumnGuard
    guard = FOSColumnGuard(list(fos_raw.columns))
    guard_report = guard.report()
    log(f"FOS column guard: {guard_report}")
    if not guard_report["safe"]:
        missing = guard_report.get("missing_expected", [])
        mapped = guard_report.get("mapped_columns", {})
        return {"error": f"FOS column mismatch. Missing expected: {missing}. Mapped: {mapped}"}
    if guard_report["warning"]:
        log(f"FOS column warnings — unexpected: {guard_report.get('unexpected_columns', [])}")
    fos_df = guard.rename_df(fos_raw)
    # ─────────────────────────────────────────────────────────────

    log(f"FOS rows: {len(fos_df):,}")

    # Clean APNs using canonical name
    fos_df['apn'] = fos_df['apn'].astype(str).str.strip()
    fos_df['apn'] = fos_df['apn'].replace(['nan', 'None', ''], pd.NA)

    # Build APN lookup
    apn_lookup = {}
    for _, row in fos_df[fos_df['apn'].notna()].iterrows():
        apn = str(row['apn']).strip()
        if len(apn) >= 8:
            apn_lookup[apn] = {
                'stock_name': row.get('stock_name', ''),
                'full_name': row.get('full_name', ''),
                'cost': row.get('cost', 0), 'avg_cost': row.get('avg_cost', 0),
                'sell_price': row.get('sell_price', 0), 'soh': row.get('soh', 0),
                'margin_pct': row.get('margin_pct', 0),
                'categories': row.get('categories', ''), 'dept': row.get('dept', ''),
                'qty_sold': row.get('qty_sold', 0), 'sales_val': row.get('sales_val', 0),
            }
    log(f"Unique APNs in FOS: {len(apn_lookup):,}")

    conn = get_conn()
    c = conn.cursor()

    # Ensure FOS columns exist
    c.execute("SELECT name FROM pragma_table_info('canonical_products')")
    existing = {r[0] for r in c.fetchall()}
    for col, ctype in [
        ('fos_apn','TEXT'), ('fos_stock_name','TEXT'), ('fos_cost','REAL'), ('fos_avg_cost','REAL'),
        ('fos_sell_price','REAL'), ('fos_soh','REAL'), ('fos_margin_pct','REAL'),
        ('fos_categories','TEXT'), ('fos_dept','TEXT'), ('fos_qty_sold','REAL'), ('fos_sales_val','REAL'),
        ('fos_match_type','TEXT'), ('fos_match_confidence','REAL'),
    ]:
        if col not in existing:
            c.execute(f"ALTER TABLE canonical_products ADD COLUMN {col} {ctype}")
    c.execute("""
        CREATE TABLE IF NOT EXISTS fos_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_id INTEGER, fos_apn TEXT,
            fos_stock_name TEXT, fos_full_name TEXT, match_type TEXT,
            match_confidence REAL, enriched_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(canonical_id, fos_apn))
    """)
    conn.commit()

    # Stage 1: Exact APN match
    exact_matches = 0
    for apn, data in apn_lookup.items():
        if stop_event and stop_event.is_set():
            break
        c.execute("SELECT id FROM canonical_products WHERE canonical_barcode=? AND fos_apn IS NULL", (apn,))
        for (cid,) in c.fetchall():
            c.execute("""
                UPDATE canonical_products SET
                    fos_apn=?, fos_stock_name=?, fos_cost=?, fos_avg_cost=?, fos_sell_price=?,
                    fos_soh=?, fos_margin_pct=?, fos_categories=?, fos_dept=?, fos_qty_sold=?,
                    fos_sales_val=?, fos_match_type='apn_exact', fos_match_confidence=1.0
                WHERE id=?
            """, (apn, data['stock_name'], data['cost'], data['avg_cost'], data['sell_price'],
                  data['soh'], data['margin_pct'], data['categories'], data['dept'],
                  data['qty_sold'], data['sales_val'], cid))
            c.execute("INSERT OR IGNORE INTO fos_matches VALUES (NULL,?,?,?,?,'apn_exact',1.0,CURRENT_TIMESTAMP)",
                      (cid, apn, data['stock_name'], data['full_name']))
            exact_matches += 1
        if exact_matches % 500 == 0:
            conn.commit()
    conn.commit()
    log(f"Exact APN matches: {exact_matches}")

    # Stage 2: Fuzzy name match for no-APN canonicals
    c.execute("SELECT id, canonical_name FROM canonical_products WHERE fos_apn IS NULL AND (canonical_barcode IS NULL OR canonical_barcode='')")
    fuzzy_candidates = c.fetchall()
    log(f"Fuzzy matching {len(fuzzy_candidates)} unenriched canonicals...")

    fos_names = []
    for _, row in fos_df[fos_df['full_name'].notna()].iterrows():
        fos_names.append((str(row['apn']) if pd.notna(row['apn']) else '', row['full_name'], row['stock_name'],
                          norm_str(row['full_name'])))

    fuzzy_matches = 0
    for cid, c_name in fuzzy_candidates:
        if stop_event and stop_event.is_set():
            break
        c_norm = norm_str(c_name)
        best_score, best_match = 0, None
        for apn, f_name, f_stock, f_norm in fos_names:
            if not f_norm:
                continue
            score = rapidfuzz_fuzz.ratio(c_norm, f_norm)
            if score > best_score:
                best_score, best_match = score, (apn, f_name, f_stock)
        if best_score >= 85 and best_match:
            apn, f_name, f_stock = best_match
            data = apn_lookup.get(apn, {})
            c.execute("""
                UPDATE canonical_products SET
                    fos_apn=?, fos_stock_name=?, fos_cost=?, fos_avg_cost=?, fos_sell_price=?,
                    fos_soh=?, fos_margin_pct=?, fos_categories=?, fos_dept=?, fos_qty_sold=?,
                    fos_sales_val=?, fos_match_type='name_fuzzy', fos_match_confidence=?
                WHERE id=?
            """, (apn, f_stock, data.get('cost',0), data.get('avg_cost',0), data.get('sell_price',0),
                  data.get('soh',0), data.get('margin_pct',0), data.get('categories',''),
                  data.get('dept',''), data.get('qty_sold',0), data.get('sales_val',0),
                  best_score/100.0, cid))
            c.execute("INSERT OR IGNORE INTO fos_matches VALUES (NULL,?,?,?,?,'name_fuzzy',?,CURRENT_TIMESTAMP)",
                      (cid, apn, f_stock, f_name, best_score/100.0))
            fuzzy_matches += 1
            if fuzzy_matches % 100 == 0:
                conn.commit()
                if msg_queue:
                    msg_queue.put({"type": "enrich_progress", "fuzzy": fuzzy_matches})
    conn.commit()
    log(f"Fuzzy name matches: {fuzzy_matches}")

    c.execute("SELECT COUNT(*) FROM canonical_products WHERE fos_apn IS NOT NULL")
    total_enriched = c.fetchone()[0]
    close_conn(conn)
    return {"exact": exact_matches, "fuzzy": fuzzy_matches, "total_enriched": total_enriched}


# ═══════════════════════════════════════════════════════════════════
#  CROSS-DOMAIN BARCODE COPY
# ═══════════════════════════════════════════════════════════════════

def cross_domain_barcode_merge(msg_queue: Optional[queue.Queue] = None,
                                stop_event: Optional[threading.Event] = None) -> Dict[str, Any]:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
    conn = get_conn()
    c = conn.cursor()

    # Find no-barcode canonicals
    c.execute("SELECT id, canonical_name, canonical_brand FROM canonical_products WHERE canonical_barcode IS NULL OR canonical_barcode=''")
    no_bc = c.fetchall()
    log(f"Cross-domain merge: {len(no_bc)} canonicals without barcode")

    # Build barcoded source index
    c.execute("SELECT sp.id, sp.barcode, sp.product_name, sp.brand, sp.source_domain FROM source_products sp WHERE sp.barcode IS NOT NULL AND sp.barcode!=''")
    barcoded = c.fetchall()
    log(f"Barcoded source products: {len(barcoded)}")

    # Build barcode -> canonical_id map
    c.execute("SELECT id, canonical_barcode FROM canonical_products WHERE canonical_barcode IS NOT NULL AND canonical_barcode!=''")
    bc_canon = {r[1]: r[0] for r in c.fetchall()}

    merged = 0
    for cid, c_name, c_brand in no_bc:
        if stop_event and stop_event.is_set():
            break
        c_norm = norm_str(c_name)
        c_brand_norm = norm_str(c_brand or '')
        best_score, best_barcode = 0, None

        for sid, barcode, s_name, s_brand, s_domain in barcoded:
            s_norm = norm_str(s_name)
            if c_brand_norm and norm_str(s_brand or '') != c_brand_norm:
                continue
            score = rapidfuzz_fuzz.ratio(c_norm, s_norm)
            if score > best_score:
                best_score, best_barcode = score, barcode

        if best_score >= 85 and best_barcode and best_barcode in bc_canon:
            target_id = bc_canon[best_barcode]
            # Move sources from cid to target_id
            c.execute("SELECT source_product_id FROM canonical_sources WHERE canonical_id=?", (cid,))
            for (sid,) in c.fetchall():
                c.execute("UPDATE canonical_sources SET canonical_id=? WHERE source_product_id=? AND canonical_id=?",
                          (target_id, sid, cid))
            # Delete empty canonical
            c.execute("DELETE FROM canonical_products WHERE id=?", (cid,))
            merged += 1
            if merged % 100 == 0:
                conn.commit()
                if msg_queue:
                    msg_queue.put({"type": "crossdomain_progress", "merged": merged})

    conn.commit()
    c.execute("SELECT COUNT(*) FROM canonical_products")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM canonical_products WHERE canonical_barcode IS NOT NULL AND canonical_barcode!=''")
    with_bc = c.fetchone()[0]
    close_conn(conn)
    log(f"Cross-domain merge complete: {merged} merged. Total canonicals: {total}, with barcode: {with_bc} ({with_bc/total*100:.1f}%)")
    return {"merged": merged, "total_canonical": total, "with_barcode": with_bc, "barcode_pct": with_bc/total*100}


# ═══════════════════════════════════════════════════════════════════
#  PRICE ANALYSIS (with size-mismatch detection)
# ═══════════════════════════════════════════════════════════════════

PACK_RE = re.compile(
    r"(\d+)\s*(?:Pack|Capsules?|Tablets?|Softgel|Soft\s*Gels?|Chewable|Effervescent|"
    r"Sachets?|Lozenges?|Units?|Amps?|Ampoules?|Vials?|Injections?|Strips?|"
    r"Pouches?|Doses?|Applications?|Patches?|Suppositories?|g|mg|mcg|kg|mL|L|oz|lb|ft|mm|cm|m|IU| Tablets| capsules| caps)",
    re.IGNORECASE)
QTY_RE = re.compile(r"(?:\bx\s*|\s)(\d+)\s*(?:Pack|pk|Capsules?|Tablets?|Caps?|Softgel|Sachets?|Lozenges?|Units?|g\b|mg\b|mcg\b|mL\b|L\b|oz\b|kg\b)", re.I)


def extract_pack(name: str) -> Tuple[Optional[int], Optional[str]]:
    if not name:
        return None, None
    m = PACK_RE.search(name)
    if m:
        return int(m.group(1)), m.group(0).replace(m.group(1), "").strip().lower()
    m2 = QTY_RE.search(name)
    if m2:
        return int(m2.group(1)), "qty"
    return None, None


def name_without_pack(name: str) -> str:
    if not name:
        return ""
    clean = re.sub(r"\d+\s*(?:Pack|pk|Capsules?|Tablets?|Softgel|Sachets?|Lozenges?|Units?|g|mg|mcg|mL|L|oz|kg|IU|ft|mm|cm|m|Strips?|Doses?|Applications?|Patches?)", "", name, flags=re.I)
    return re.sub(r"\s+", " ", clean).strip()


def size_confidence(fos_name: str, comp_name: str, fos_qty, comp_qty) -> Tuple[float, str]:
    base_fos = name_without_pack(fos_name).lower()
    base_comp = name_without_pack(comp_name).lower()
    if base_fos == base_comp and fos_qty and comp_qty and fos_qty != comp_qty:
        return 0.1, "different_pack_size"
    if fos_qty and comp_qty and fos_qty == comp_qty:
        return 1.0, "same_pack_size"
    if (fos_qty and not comp_qty) or (not fos_qty and comp_qty):
        return 0.7, "qty_unclear"
    return 0.8, "no_qty_info"


def price_analysis(msg_queue: Optional[queue.Queue] = None,
                   stop_event: Optional[threading.Event] = None) -> Dict[str, Any]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT id, canonical_name, canonical_brand, canonical_barcode, canonical_category,
               fos_sell_price, fos_cost, fos_avg_cost, fos_dept, fos_soh, fos_qty_sold, fos_sales_val
        FROM canonical_products WHERE fos_sell_price IS NOT NULL
    """)
    fos_rows = c.fetchall()

    c.execute("""
        SELECT cs.canonical_id, sp.source_domain, sp.source_name, sp.product_name,
               sp.brand, sp.current_price, sp.sale_price, sp.pack_size, sp.product_url
        FROM canonical_sources cs JOIN source_products sp ON cs.source_product_id = sp.id
        WHERE sp.current_price IS NOT NULL
    """)
    source_rows = c.fetchall()

    sources_by_canon = defaultdict(list)
    for r in source_rows:
        sources_by_canon[r["canonical_id"]].append(r)

    records = []
    stats = defaultdict(int)

    for canon in fos_rows:
        if stop_event and stop_event.is_set():
            break
        cid = canon["id"]
        fos_name = canon["canonical_name"] or ""
        fos_price = to_float(canon["fos_sell_price"])
        fos_cost = to_float(canon["fos_cost"])
        fos_qty, fos_unit = extract_pack(fos_name)

        comp_prices = []
        mismatch_flags = []
        for src in sources_by_canon.get(cid, []):
            src_price = to_float(src["current_price"])
            src_sale = to_float(src["sale_price"])
            use_price = src_sale if src_sale and src_sale < src_price else src_price
            if use_price is None or use_price <= 0:
                continue
            src_name = src["product_name"] or ""
            src_qty, src_unit = extract_pack(src_name)
            conf, reason = size_confidence(fos_name, src_name, fos_qty, src_qty)
            comp_prices.append({"price": use_price, "match_confidence": conf, "match_reason": reason, "domain": src["source_domain"]})
            if conf < 0.5:
                mismatch_flags.append(f"{src['source_domain']}: {reason}")

        if not comp_prices:
            stats["no_competitor_data"] += 1
            continue

        high_conf = [p for p in comp_prices if p["match_confidence"] >= 0.5] or comp_prices
        prices = [p["price"] for p in high_conf]
        min_p, max_p = min(prices), max(prices)
        avg_p = sum(prices) / len(prices)
        median_p = sorted(prices)[len(prices)//2]

        if fos_price is None or fos_price <= 0:
            position = "no_fos_price"
        elif fos_price < min_p:
            position = "lowest"
        elif fos_price > max_p:
            position = "highest"
        elif abs(fos_price - avg_p) < 0.01:
            position = "at_avg"
        elif fos_price < avg_p:
            position = "below_avg"
        else:
            position = "above_avg"

        gap = (fos_price - avg_p) if fos_price else None
        gap_pct = ((fos_price - avg_p) / avg_p * 100) if (fos_price and avg_p > 0) else None

        source_summary = "; ".join(f"{p['domain']}: ${p['price']:.2f}" for p in high_conf[:5])
        if len(high_conf) > 5:
            source_summary += f"; +{len(high_conf)-5} more"

        records.append({
            "canonical_id": cid, "canonical_name": fos_name,
            "canonical_brand": canon["canonical_brand"], "barcode": canon["canonical_barcode"],
            "category": canon["canonical_category"] or canon["fos_dept"] or "",
            "fos_sell_price": fos_price, "fos_cost": fos_cost,
            "fos_soh": canon["fos_soh"], "fos_qty_sold": canon["fos_qty_sold"],
            "fos_sales_value": to_float(canon["fos_sales_val"]),
            "fos_pack_qty": fos_qty, "fos_unit": fos_unit,
            "competitor_count": len(high_conf), "comp_min": min_p,
            "comp_max": max_p, "comp_avg": round(avg_p, 2), "comp_median": median_p,
            "price_position": position, "price_gap": round(gap, 2) if gap else None,
            "price_gap_pct": round(gap_pct, 1) if gap_pct else None,
            "source_summary": source_summary,
            "mismatch_flags": " | ".join(mismatch_flags) if mismatch_flags else "",
            "low_conf_sources": len([p for p in comp_prices if p["match_confidence"] < 0.5]),
        })
        stats[f"pos_{position}"] += 1

    df = pd.DataFrame(records)
    total = len(df)

    # Build sheets
    underpriced = df[(df["price_position"].isin(["lowest", "below_avg"])) & (df["fos_sell_price"] > 0)].sort_values("price_gap")
    overpriced = df[(df["price_position"].isin(["highest", "above_avg"])) & (df["fos_sell_price"] > 0)].sort_values("price_gap", ascending=False)
    size_mismatch = df[df["mismatch_flags"] != ""].sort_values("price_gap", ascending=False)
    at_avg = df[df["price_position"] == "at_avg"]
    no_price = df[df["price_position"] == "no_fos_price"]
    cat_summary = df.groupby("category").agg({
        "fos_sell_price": "mean", "comp_avg": "mean", "price_gap": "mean", "canonical_id": "count"
    }).rename(columns={"canonical_id": "count"}).sort_values("count", ascending=False)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = EXPORT_DIR / f"price_analysis_definitive_{timestamp}.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="All_Products", index=False)
        underpriced.to_excel(writer, sheet_name="Underpriced_Opportunities", index=False)
        overpriced.to_excel(writer, sheet_name="Overpriced_Risk", index=False)
        size_mismatch.to_excel(writer, sheet_name="Size_Mismatch_Flagged", index=False)
        at_avg.to_excel(writer, sheet_name="At_Average", index=False)
        no_price.to_excel(writer, sheet_name="No_FOS_Price", index=False)
        cat_summary.to_excel(writer, sheet_name="Category_Summary")

    close_conn(conn)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    log(f"Price analysis exported: {out_path} ({size_mb:.1f} MB, {total} rows)")
    return {"path": str(out_path), "rows": total, "underpriced": len(underpriced),
            "overpriced": len(overpriced), "size_mismatch": len(size_mismatch), "stats": dict(stats)}


# ═══════════════════════════════════════════════════════════════════
#  EXPORT WORKBOOK
# ═══════════════════════════════════════════════════════════════════

def export_workbook(msg_queue: Optional[queue.Queue] = None) -> Dict[str, Any]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = EXPORT_DIR / f"canonical_master_{timestamp}.xlsx"

    # Sheet 1: Canonical_Products
    c.execute("""
        SELECT cp.id AS canonical_id, cp.canonical_name, cp.canonical_brand, cp.canonical_barcode,
               cp.canonical_size, cp.canonical_category, cp.canonical_subcategory, cp.match_type,
               cp.match_confidence, cp.quality_flags, cp.created_at, cp.updated_at,
               cp.fos_apn, cp.fos_stock_name, cp.fos_cost, cp.fos_avg_cost, cp.fos_sell_price,
               cp.fos_soh, cp.fos_margin_pct, cp.fos_categories, cp.fos_dept,
               cp.fos_qty_sold, cp.fos_sales_val, cp.fos_match_type, cp.fos_match_confidence,
               COUNT(cs.source_product_id) AS source_count,
               GROUP_CONCAT(DISTINCT sp.source_domain) AS source_domains,
               MIN(sp.current_price) AS min_price, MAX(sp.current_price) AS max_price,
               AVG(sp.current_price) AS avg_price,
               COUNT(DISTINCT sp.source_domain) AS num_competitors,
               MAX(sp.image_url) AS sample_image
        FROM canonical_products cp
        LEFT JOIN canonical_sources cs ON cp.id = cs.canonical_id
        LEFT JOIN source_products sp ON cs.source_product_id = sp.id
        GROUP BY cp.id
    """)
    can_df = pd.DataFrame([dict(r) for r in c.fetchall()])

    # Sheet 2: Source_Products
    c.execute("""
        SELECT sp.id AS source_id, sp.scrape_batch, sp.source_name, sp.source_domain,
               sp.product_name, sp.brand, sp.barcode, sp.sku, sp.pack_size, sp.variant,
               sp.category, sp.subcategory, sp.description, sp.ingredients, sp.image_url,
               sp.product_url, sp.current_price, sp.sale_price, sp.currency, sp.stock_status,
               sp.scraped_at, sp.quality_flags, cs.canonical_id
        FROM source_products sp LEFT JOIN canonical_sources cs ON sp.id = cs.source_product_id
    """)
    src_df = pd.DataFrame([dict(r) for r in c.fetchall()])

    # Sheet 3: Price_Comparison
    c.execute("""
        SELECT cp.id AS canonical_id, cp.canonical_name, cp.canonical_brand, cp.canonical_barcode,
               cp.canonical_size, sp.source_name, sp.source_domain, sp.product_name AS source_product_name,
               sp.product_url AS source_url, sp.current_price AS price, sp.sale_price,
               sp.currency, sp.stock_status, sp.scraped_at,
               cp.fos_apn, cp.fos_sell_price AS fos_my_sell_price,
               cp.fos_cost AS fos_my_cost, cp.fos_margin_pct AS fos_my_margin_pct
        FROM canonical_products cp
        JOIN canonical_sources cs ON cp.id = cs.canonical_id
        JOIN source_products sp ON cs.source_product_id = sp.id
        WHERE sp.current_price > 0
    """)
    price_df = pd.DataFrame([dict(r) for r in c.fetchall()])

    # Sheet 4: Shopify_Ready
    c.execute("""
        SELECT cp.id AS canonical_id, cp.canonical_name AS Title, cp.canonical_brand AS Vendor,
               cp.canonical_category AS 'Product Type', cp.canonical_barcode AS 'Variant Barcode',
               cp.canonical_size AS 'Option1 Value', '' AS 'Option2 Value', '' AS 'Option3 Value',
               '' AS Tags, cp.canonical_name AS 'SEO Title', cp.canonical_category AS 'SEO Description',
               (SELECT MAX(image_url) FROM source_products sp2 JOIN canonical_sources cs2 ON sp2.id = cs2.source_product_id WHERE cs2.canonical_id = cp.id) AS 'Image Src',
               (SELECT MIN(current_price) FROM source_products sp3 JOIN canonical_sources cs3 ON sp3.id = cs3.source_product_id WHERE cs3.canonical_id = cp.id AND sp3.current_price > 0) AS 'Variant Price',
               'AUD' AS 'Variant Currency', 'draft' AS Status, cp.fos_apn,
               cp.fos_cost AS 'FOS Cost', cp.fos_sell_price AS 'FOS Sell Price',
               cp.fos_margin_pct AS 'FOS Margin %', cp.fos_soh AS 'FOS Stock On Hand'
        FROM canonical_products cp
    """)
    shopify_df = pd.DataFrame([dict(r) for r in c.fetchall()])

    # Sheet 5: eBay_Ready
    c.execute("""
        SELECT cp.id AS canonical_id, 'Add' AS Action, cp.canonical_name AS Title,
               cp.canonical_brand AS Brand, cp.canonical_barcode AS 'UPC/EAN',
               cp.canonical_size AS 'Item Specific:Size', cp.canonical_category AS Category,
               cp.canonical_description AS Description,
               (SELECT MIN(current_price) FROM source_products sp JOIN canonical_sources cs ON sp.id = cs.source_product_id WHERE cs.canonical_id = cp.id AND sp.current_price > 0) AS 'Start Price',
               (SELECT MAX(image_url) FROM source_products sp JOIN canonical_sources cs ON sp.id = cs.source_product_id WHERE cs.canonical_id = cp.id) AS PictureURL,
               'FixedPriceItem' AS 'Listing Type', 'GTC' AS Duration, 'AU' AS Country,
               '3000' AS PostalCode, 'Flat' AS 'Shipping Type', 0 AS 'Shipping Cost', 'AUD' AS Currency,
               cp.fos_apn, cp.fos_cost AS 'FOS Cost', cp.fos_sell_price AS 'FOS Sell Price',
               cp.fos_margin_pct AS 'FOS Margin %', cp.fos_soh AS 'FOS Stock On Hand'
        FROM canonical_products cp
    """)
    ebay_df = pd.DataFrame([dict(r) for r in c.fetchall()])

    close_conn(conn)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        can_df.to_excel(writer, sheet_name="Canonical_Products", index=False)
        src_df.to_excel(writer, sheet_name="Source_Products", index=False)
        price_df.to_excel(writer, sheet_name="Price_Comparison", index=False)
        shopify_df.to_excel(writer, sheet_name="Shopify_Ready", index=False)
        ebay_df.to_excel(writer, sheet_name="eBay_Ready", index=False)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    log(f"Master workbook exported: {out_path} ({size_mb:.1f} MB)")
    return {"path": str(out_path), "canonical": len(can_df), "source": len(src_df),
            "price": len(price_df), "shopify": len(shopify_df), "ebay": len(ebay_df)}


# ═══════════════════════════════════════════════════════════════════
#  STATS / DASHBOARD DATA
# ═══════════════════════════════════════════════════════════════════

def get_dashboard_stats() -> Dict[str, Any]:
    conn = get_conn()
    c = conn.cursor()
    stats = {}
    c.execute("SELECT COUNT(*) FROM source_products")
    stats["total_scraped"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM source_products WHERE barcode IS NOT NULL AND barcode!=''")
    stats["with_barcodes"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM canonical_products")
    stats["canonical"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM canonical_products WHERE fos_apn IS NOT NULL")
    stats["fos_enriched"] = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT source_domain) FROM source_products")
    stats["sites_scraped"] = c.fetchone()[0]
    close_conn(conn)
    return stats


# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════

def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "db_path": str(DB_PATH), "export_dir": str(EXPORT_DIR),
        "worker_threads": 4, "timeout": 15, "aggressive_barcode": True,
        "max_html_products": 100, "fos_path": "",
    }


def save_config(cfg: Dict[str, Any]):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)
