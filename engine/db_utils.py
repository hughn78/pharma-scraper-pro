#!/usr/bin/env python3
"""
Pharma Scraper Pro — Database Utilities & Guards
=================================================
Thread-safe connection pool, schema indexes, validation layer,
FOS CSV column guard, and automatic quality flagging.
"""

import os, re, sqlite3, threading, json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

# ── Connection Pool ───────────────────────────────────────────────

class ConnectionPool:
    """Thread-safe SQLite connection pool with WAL mode."""
    def __init__(self, db_path: Path, max_conn: int = 4):
        self.db_path = db_path
        self.max_conn = max_conn
        self._pool: List[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._used = 0
        for _ in range(max_conn):
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-64000")  # 64 MB
            self._pool.append(conn)

    def acquire(self) -> sqlite3.Connection:
        with self._lock:
            if self._pool:
                self._used += 1
                return self._pool.pop()
            # Fallback: create overflow connection
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            self._used += 1
            return conn

    def release(self, conn: sqlite3.Connection):
        with self._lock:
            self._used -= 1
            self._pool.append(conn)

    def close_all(self):
        with self._lock:
            for conn in self._pool:
                conn.close()
            self._pool.clear()

    def __del__(self):
        self.close_all()

# Global pool instance (set by core.py after DB path known)
_pool: Optional[ConnectionPool] = None

def set_pool(db_path: Path, max_conn: int = 4):
    global _pool
    _pool = ConnectionPool(db_path, max_conn)

def get_pooled_conn() -> sqlite3.Connection:
    if _pool is None:
        raise RuntimeError("Connection pool not initialized. Call set_pool() first.")
    return _pool.acquire()

def release_conn(conn: sqlite3.Connection):
    if _pool:
        _pool.release(conn)


# ── Schema Indexes ────────────────────────────────────────────────

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_source_barcode ON source_products(barcode);
CREATE INDEX IF NOT EXISTS idx_source_domain ON source_products(source_domain);
CREATE INDEX IF NOT EXISTS idx_source_name ON source_products(product_name);
CREATE INDEX IF NOT EXISTS idx_canon_barcode ON canonical_products(canonical_barcode);
CREATE INDEX IF NOT EXISTS idx_canon_brand_size ON canonical_products(canonical_brand, canonical_size);
CREATE INDEX IF NOT EXISTS idx_canon_name ON canonical_products(canonical_name);
CREATE INDEX IF NOT EXISTS idx_fos_apn ON canonical_products(fos_apn);
CREATE INDEX IF NOT EXISTS idx_cs_canon ON canonical_sources(canonical_id);
CREATE INDEX IF NOT EXISTS idx_cs_domain ON canonical_sources(source_domain);
CREATE INDEX IF NOT EXISTS idx_fm_apn ON fos_matches(fos_apn);
"""

def ensure_indexes(conn: sqlite3.Connection):
    conn.executescript(INDEX_SQL)
    conn.commit()


# ── Schema Validation ─────────────────────────────────────────────

@dataclass
class ValidationError:
    field: str
    rule: str
    value: Any

class SourceProductValidator:
    """Validate scraped product dicts before DB insertion."""

    BARCODE_PATTERNS = {
        "ean13": re.compile(r"^\d{13}$"),
        "upc_a": re.compile(r"^\d{12}$"),
        "ean8": re.compile(r"^\d{8}$"),
        "itf14": re.compile(r"^\d{14}$"),
    }

    REQUIRED_FIELDS = ["product_name", "product_url"]
    NUMERIC_FIELDS = ["current_price", "sale_price"]

    @classmethod
    def validate(cls, data: Dict[str, Any]) -> Tuple[bool, List[ValidationError], List[str]]:
        errors: List[ValidationError] = []
        flags: List[str] = []

        # Required fields
        for field in cls.REQUIRED_FIELDS:
            if not data.get(field) or not str(data[field]).strip():
                errors.append(ValidationError(field, "required_missing", data.get(field)))
                flags.append("missing_required")

        # Barcode validation
        barcode = str(data.get("barcode", "")).strip()
        if barcode:
            if not any(p.match(barcode) for p in cls.BARCODE_PATTERNS.values()):
                errors.append(ValidationError("barcode", "invalid_length_or_format", barcode))
                flags.append("barcode_malformed")
            elif not cls._ean13_check_digit(barcode):
                errors.append(ValidationError("barcode", "check_digit_failed", barcode))
                flags.append("barcode_checksum_fail")
        else:
            flags.append("missing_barcode")

        # Price validation
        for field in cls.NUMERIC_FIELDS:
            val = data.get(field)
            if val is not None and val != "":
                try:
                    fval = float(val)
                    if fval < 0:
                        errors.append(ValidationError(field, "negative_price", val))
                        flags.append("negative_price")
                    elif fval == 0:
                        flags.append("zero_price")
                except (ValueError, TypeError):
                    errors.append(ValidationError(field, "non_numeric_price", val))
                    flags.append("price_unparseable")

        # Name length sanity
        name = str(data.get("product_name", "")).strip()
        if len(name) > 500:
            flags.append("name_extremely_long")
        if len(name) < 3:
            flags.append("name_suspiciously_short")

        # URL sanity
        url = str(data.get("product_url", "")).strip()
        if url and not (url.startswith("http://") or url.startswith("https://")):
            errors.append(ValidationError("product_url", "invalid_protocol", url))
            flags.append("url_invalid")

        is_valid = len([e for e in errors if e.rule in ("required_missing", "negative_price")]) == 0
        return is_valid, errors, flags

    @staticmethod
    def _ean13_check_digit(barcode: str) -> bool:
        if len(barcode) != 13 or not barcode.isdigit():
            return False
        odd = sum(int(barcode[i]) for i in range(0, 12, 2))
        even = sum(int(barcode[i]) for i in range(1, 12, 2))
        check = (10 - ((odd + even * 3) % 10)) % 10
        return check == int(barcode[12])


def validate_scraped_data(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Quick validation returning (is_ok, quality_flags_list)."""
    ok, errors, flags = SourceProductValidator.validate(data)
    return ok, flags


# ── FOS CSV Column Guard ──────────────────────────────────────────

EXPECTED_FOS_COLUMNS = {
    "apn", "stock_name", "description", "stock_group", "department",
    "qty_sold", "total_cost", "total_sales", "total_gp", "gp_margin",
    "soh", "cost", "avg_cost", "sell_price",
    # Aliases
    "barcode", "name", "product_name", "fos_apn", "fos_stock_name",
}

class FOSColumnGuard:
    """Detect when FOS CSV headers deviate from expected schema."""

    def __init__(self, actual_columns: List[str]):
        self.actual = set(c.lower().strip().replace(" ", "_") for c in actual_columns)
        self.missing = EXPECTED_FOS_COLUMNS - self.actual
        self.unexpected = self.actual - EXPECTED_FOS_COLUMNS
        self.mapped = self._build_map(actual_columns)

    def _build_map(self, cols: List[str]) -> Dict[str, str]:
        """Map canonical names to actual CSV column names."""
        mapping = {}
        lower_cols = {c.lower().strip().replace(" ", "_"): c for c in cols}
        aliases = {
            "apn": ["apn", "barcode", "ean", "gtin", "fos_apn"],
            "stock_name": ["stock_name", "name", "product_name", "description", "fos_stock_name"],
            "sell_price": ["sell_price", "price", "fos_sell_price", "retail_price"],
            "cost": ["cost", "fos_cost", "total_cost"],
            "avg_cost": ["avg_cost", "fos_avg_cost", "average_cost"],
            "soh": ["soh", "stock_on_hand", "qty_on_hand", "fos_soh"],
            "qty_sold": ["qty_sold", "quantity_sold", "units_sold"],
            "total_sales": ["total_sales", "sales_value", "sales"],
            "department": ["department", "dept", "fos_dept"],
            "stock_group": ["stock_group", "category", "fos_categories"],
        }
        for canonical, candidates in aliases.items():
            for cand in candidates:
                if cand in lower_cols:
                    mapping[canonical] = lower_cols[cand]
                    break
        return mapping

    def is_safe(self) -> bool:
        # Must have at least APN/Barcode and some name/price fields
        has_key = bool(self.mapped.get("apn") or self.mapped.get("stock_name"))
        has_price = bool(self.mapped.get("sell_price") or self.mapped.get("cost"))
        return has_key and has_price

    def report(self) -> Dict[str, Any]:
        return {
            "safe": self.is_safe(),
            "mapped_columns": self.mapped,
            "missing_expected": sorted(self.missing),
            "unexpected_columns": sorted(self.unexpected),
            "warning": bool(self.missing or self.unexpected),
        }

    def rename_df(self, df) -> Any:
        """Rename DataFrame columns to canonical names."""
        rename_map = {v: k for k, v in self.mapped.items()}
        return df.rename(columns=rename_map)


# ── Quality Flag Auto-Assigner ────────────────────────────────────

QUALITY_RULES = {
    "missing_barcode": lambda d: not str(d.get("barcode", "")).strip(),
    "missing_brand": lambda d: not str(d.get("brand", "")).strip(),
    "missing_price": lambda d: not d.get("current_price") and not d.get("sale_price"),
    "zero_price": lambda d: float(d.get("current_price") or 0) == 0 or float(d.get("sale_price") or 0) == 0,
    "missing_image": lambda d: not str(d.get("image_url", "")).strip(),
    "missing_category": lambda d: not str(d.get("category", "")).strip(),
    "name_suspiciously_short": lambda d: len(str(d.get("product_name", "")).strip()) < 5,
    "name_extremely_long": lambda d: len(str(d.get("product_name", "")).strip()) > 300,
}

def auto_quality_flags(data: Dict[str, Any]) -> str:
    flags = []
    for flag, test in QUALITY_RULES.items():
        try:
            if test(data):
                flags.append(flag)
        except Exception:
            pass
    # Add validation-derived flags
    _, _, vflags = SourceProductValidator.validate(data)
    flags.extend(vflags)
    return ";".join(sorted(set(flags))) if flags else ""


# ── Pack-Size Normalization (for canonical matching) ──────────────

SIZE_UNITS = [
    (r"(\d+)\s*(?:tablets?|tabs?|caplets?|capsules?|caps?)", r"\1 tablets"),
    (r"(\d+)\s*(?:softgels?|soft\s*gels?)", r"\1 softgels"),
    (r"(\d+)\s*(?:chewables?|chews?)", r"\1 chewables"),
    (r"(\d+)\s*(?:gummies?|gummy)", r"\1 gummies"),
    (r"(\d+(?:\.\d+)?)\s*(?:ml|mL|millilitres?|milliliters?)", r"\1 mL"),
    (r"(\d+(?:\.\d+)?)\s*(?:g|grams?|grammes?)", r"\1 g"),
    (r"(\d+(?:\.\d+)?)\s*(?:kg|kilos?|kilograms?)", r"\1 kg"),
    (r"(\d+(?:\.\d+)?)\s*(?:l|L|litres?|liters?)", r"\1 L"),
    (r"(\d+)\s*(?:sachets?|sachs?)", r"\1 sachets"),
    (r"(\d+)\s*(?:packs?|pk|pks)", r"\1 pack"),
    (r"(\d+)\s*(?:pairs?|pr)", r"\1 pair"),
    (r"(\d+)\s*(?:strips?|strps?)", r"\1 strips"),
]

def normalize_pack_size(text: str) -> str:
    if not text:
        return ""
    text = str(text).lower().strip()
    for pattern, replacement in SIZE_UNITS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# ── Export ───────────────────────────────────────────────────────
__all__ = [
    "ConnectionPool", "set_pool", "get_pooled_conn", "release_conn",
    "ensure_indexes",
    "SourceProductValidator", "validate_scraped_data", "ValidationError",
    "FOSColumnGuard", "EXPECTED_FOS_COLUMNS",
    "auto_quality_flags", "normalize_pack_size",
]
