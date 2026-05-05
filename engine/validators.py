#!/usr/bin/env python3
"""
Pharma Scraper Pro — Validation & Utilities
==========================================
Barcode validation, proxy rotation, error reporting, and performance utilities.
"""

import re
from typing import List, Optional, Tuple, Dict, Any

# ═══════════════════════════════════════════════════════════════════
#  BARCODE VALIDATION (EAN-13 / UPC-A / GTIN-14 check digits)
# ═══════════════════════════════════════════════════════════════════

class BarcodeValidator:
    """Validate GTIN barcodes with proper check-digit verification."""

    @staticmethod
    def gtin_check_digit(gtin: str) -> bool:
        """Verify GTIN-8/12/13/14 check digit."""
        if not gtin or not gtin.isdigit():
            return False
        if len(gtin) not in (8, 12, 13, 14):
            return False
        # Remove check digit
        payload = gtin[:-1]
        expected = BarcodeValidator._compute_check_digit(payload)
        return expected == gtin[-1]

    @staticmethod
    def _compute_check_digit(payload: str) -> str:
        """Compute GTIN check digit for a payload string."""
        total = 0
        for i, ch in enumerate(reversed(payload)):
            digit = int(ch)
            if i % 2 == 0:
                total += digit * 3
            else:
                total += digit
        check = (10 - (total % 10)) % 10
        return str(check)

    @staticmethod
    def normalize(raw: str) -> str:
        """Strip to digits, validate length, verify check digit. Returns valid GTIN or empty string."""
        if not raw:
            return ""
        # Handle scientific notation from Excel
        val = str(raw).strip().upper()
        if "E+" in val or "e+" in val:
            try:
                val = str(int(float(val)))
            except Exception:
                pass
        val = re.sub(r"\D", "", val)
        if len(val) in (8, 12, 13, 14) and BarcodeValidator.gtin_check_digit(val):
            return val
        # Try padding short codes
        if len(val) == 11:
            val = "0" + val
            if BarcodeValidator.gtin_check_digit(val):
                return val
        if len(val) == 10:
            val = "00" + val
            if BarcodeValidator.gtin_check_digit(val):
                return val
        return ""

    @staticmethod
    def validate_bulk(barcodes: List[str]) -> Dict[str, Any]:
        """Validate a list of barcodes and return statistics."""
        results = {"valid": [], "invalid": [], "fixed": [], "stats": {"total": len(barcodes), "valid": 0, "invalid": 0}}
        for bc in barcodes:
            clean = BarcodeValidator.normalize(bc)
            if clean:
                results["valid"].append(clean)
                results["stats"]["valid"] += 1
                if clean != re.sub(r"\D", "", str(bc)):
                    results["fixed"].append((bc, clean))
            else:
                results["invalid"].append(bc)
                results["stats"]["invalid"] += 1
        return results


# ═══════════════════════════════════════════════════════════════════
#  PROXY ROTATION
# ═══════════════════════════════════════════════════════════════════

class ProxyRotator:
    """Simple proxy rotation for scraping."""

    def __init__(self, proxies: Optional[List[str]] = None):
        self.proxies = proxies or []
        self.index = 0

    def add_proxy(self, proxy_url: str):
        """proxy_url format: http://user:pass@host:port or http://host:port"""
        if proxy_url and proxy_url not in self.proxies:
            self.proxies.append(proxy_url)

    def get_next(self) -> Optional[Dict[str, str]]:
        if not self.proxies:
            return None
        proxy = self.proxies[self.index % len(self.proxies)]
        self.index += 1
        return {"http": proxy, "https": proxy}

    def rotate_for_session(self, session) -> bool:
        """Apply next proxy to a requests.Session. Returns True if proxy was set."""
        px = self.get_next()
        if px:
            session.proxies.update(px)
            return True
        return False


# ═══════════════════════════════════════════════════════════════════
#  ERROR REPORTING
# ═══════════════════════════════════════════════════════════════════

class SiteErrorReporter:
    """Collect per-site error details for later analysis."""

    def __init__(self):
        self.errors: Dict[str, List[Dict[str, Any]]] = {}

    def log(self, domain: str, error_type: str, message: str, details: Optional[Dict] = None):
        self.errors.setdefault(domain, []).append({
            "type": error_type, "message": message, "details": details or {},
            "timestamp": __import__("datetime").datetime.now().isoformat()
        })

    def summary(self) -> str:
        lines = ["=== Site Error Summary ==="]
        for domain, errs in sorted(self.errors.items(), key=lambda x: -len(x[1])):
            types = {}
            for e in errs:
                types[e["type"]] = types.get(e["type"], 0) + 1
            lines.append(f"  {domain}: {len(errs)} errors ({', '.join(f'{k}:{v}' for k,v in sorted(types.items(), key=lambda x:-x[1]))})")
        return "\n".join(lines)

    def get_domain_errors(self, domain: str) -> List[Dict[str, Any]]:
        return self.errors.get(domain, [])

    def has_js_required(self, domain: str) -> bool:
        """Detect if a site consistently fails with 403/blank content — likely JS-rendered."""
        errs = self.errors.get(domain, [])
        if not errs:
            return False
        js_indicators = ["403", "blank", "cloudflare", "challenge", "js", "javascript", "render"]
        js_count = sum(1 for e in errs if any(i in e["message"].lower() for i in js_indicators))
        return js_count >= len(errs) * 0.5 and len(errs) >= 2

    def export_json(self, path: str):
        import json
        with open(path, "w") as f:
            json.dump(self.errors, f, indent=2)


# ═══════════════════════════════════════════════════════════════════
#  FUZZY MATCHING (rapidfuzz wrapper)
# ═══════════════════════════════════════════════════════════════════

try:
    from rapidfuzz import fuzz as rapid_fuzz
    HAVE_RAPIDFUZZ = True
except ImportError:
    HAVE_RAPIDFUZZ = False


class FuzzyMatcher:
    """High-performance fuzzy string matching with rapidfuzz fallback."""

    @staticmethod
    def ratio(a: str, b: str) -> float:
        if HAVE_RAPIDFUZZ:
            return rapid_fuzz.ratio(str(a).lower(), str(b).lower()) / 100.0
        # Fallback to difflib
        from difflib import SequenceMatcher
        return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

    @staticmethod
    def partial_ratio(a: str, b: str) -> float:
        if HAVE_RAPIDFUZZ:
            return rapid_fuzz.partial_ratio(str(a).lower(), str(b).lower()) / 100.0
        return FuzzyMatcher.ratio(a, b)

    @staticmethod
    def token_sort_ratio(a: str, b: str) -> float:
        if HAVE_RAPIDFUZZ:
            return rapid_fuzz.token_sort_ratio(str(a).lower(), str(b).lower()) / 100.0
        return FuzzyMatcher.ratio(a, b)

    @staticmethod
    def token_set_ratio(a: str, b: str) -> float:
        if HAVE_RAPIDFUZZ:
            return rapid_fuzz.token_set_ratio(str(a).lower(), str(b).lower()) / 100.0
        return FuzzyMatcher.ratio(a, b)


# ═══════════════════════════════════════════════════════════════════
#  JS-SITE DETECTOR
# ═══════════════════════════════════════════════════════════════════

JS_SITE_PATTERNS = [
    r"cloudflare", r"challenge", r"cf-browser-verification",
    r"__NEXT_DATA__", r"__NUXT__", r"window\.__INITIAL_STATE__",
    r"data-reactroot", r"ng-app", r"vue-router",
]


def is_likely_js_rendered(html_text: str) -> bool:
    """Heuristic: does this HTML suggest heavy JS rendering?"""
    if not html_text:
        return False
    text = html_text.lower()
    score = 0
    for pattern in JS_SITE_PATTERNS:
        if re.search(pattern, text, re.I):
            score += 1
    return score >= 2


# ═══════════════════════════════════════════════════════════════════
#  MANUAL SEARCH HELPER (for GUI)
# ═══════════════════════════════════════════════════════════════════

def search_canonical_by_text(conn, query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search canonical products by name or brand. Returns list of dicts."""
    c = conn.cursor()
    pattern = f"%{query}%"
    c.execute("""
        SELECT id, canonical_name, canonical_brand, canonical_barcode, canonical_size,
               canonical_category, fos_sell_price, fos_soh
        FROM canonical_products
        WHERE canonical_name LIKE ? OR canonical_brand LIKE ? OR canonical_barcode LIKE ?
        ORDER BY fos_sell_price IS NOT NULL DESC, canonical_name
        LIMIT ?
    """, (pattern, pattern, pattern, limit))
    cols = [d[0] for d in c.description]
    return [dict(zip(cols, row)) for row in c.fetchall()]


def search_fos_by_text(conn, query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search FOS matches by stock name."""
    c = conn.cursor()
    pattern = f"%{query}%"
    c.execute("""
        SELECT fm.canonical_id, fm.fos_apn, fm.fos_stock_name, fm.fos_full_name,
               fm.match_type, fm.match_confidence, cp.canonical_name, cp.canonical_barcode
        FROM fos_matches fm
        JOIN canonical_products cp ON fm.canonical_id = cp.id
        WHERE fm.fos_stock_name LIKE ? OR fm.fos_full_name LIKE ? OR fm.fos_apn LIKE ?
        LIMIT ?
    """, (pattern, pattern, pattern, limit))
    cols = [d[0] for d in c.description]
    return [dict(zip(cols, row)) for row in c.fetchall()]
