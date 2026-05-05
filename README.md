# Pharma Scraper Pro v2.0

All-in-one Python desktop application for building a canonical product database from 50+ Australian pharmacy, supplement, vitamin, and compounding websites.

**Purpose:** Scrape competitor catalogs → deduplicate into canonical products → match against your FOS stock report → analyze pricing → export to Shopify & eBay.

---

## Features

| Module | What It Does |
|--------|-------------|
| **Scraper** | Multi-threaded Shopify JSON + HTML fallback scraping from 50+ sites |
| **Canonicalisation** | Deduplicate by barcode, then fuzzy match by brand+name+size |
| **FOS Enrichment** | Match your FOS stock report (APN = barcode) to canonical products |
| **Cross-Domain Merge** | Copy barcodes from one domain's product to another domain's matching product |
| **Price Analysis** | Compare your FOS sell price vs competitor min/avg/max, flag pack-size mismatches |
| **Export** | Multi-sheet Excel: Canonical, Source, Price Comparison, Shopify-Ready, eBay-Ready |

---

## Quick Start

### 1. Install Dependencies

```bash
pip install pandas openpyxl requests beautifulsoup4 lxml thefuzz
```

Or with uv:
```bash
uv pip install pandas openpyxl requests beautifulsoup4 lxml thefuzz
```

### 2. Launch the GUI

```bash
cd engine
python3 pharma_scraper_pro.py
```

### 3. Typical Workflow

1. **Sites tab** — enable/disable target websites (50 pre-loaded)
2. **Scrape tab** — click "Start Scrape" (runs in background)
3. **Canonical tab** — click "Run Canonicalisation" to dedupe
4. **Enrich tab** — load your `FOS_Cleaned.xlsx` → click "Enrich from FOS"
5. **Enrich tab** — click "Cross-Domain Merge" to copy barcodes across domains
6. **Price Analysis tab** — click "Run Price Analysis" for competitor pricing report
7. **Export tab** — click "Export All Selected" for your workbooks

---

## Architecture

```
engine/
  core.py                 — All business logic (scrape, canonical, enrich, price, export)
  pharma_scraper_pro.py   — Tkinter GUI wrapper with 8 tabs

data/
  canonical_products.db   — SQLite database (auto-created)

exports/
  canonical_master_*.xlsx — Master 5-sheet workbook
  price_analysis_*.xlsx  — Price analysis 7-sheet workbook

config.json               — User settings (auto-saved)
```

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `source_products` | Every scraped product from every site |
| `canonical_products` | Deduplicated canonical products |
| `canonical_sources` | Link table: canonical ↔ source |
| `fos_matches` | FOS stock report match history |

---

## Pre-Loaded Sites (50)

Pharmacy Online, Chemist Direct, Pharmacy Direct, Better Value Pharmacy, Pharmacy 4 Less, Aussie Health Products, Megavitamins, Zenith Pharmacy, Michael's Chemist, National Custom Compounding, Healthylife, Chemist2U, Compounding Pharmacy Australia, TerryWhite Chemmart, Direct Chemist Outlet, Chemist Warehouse, Chemist Works, Mr Supplement, ePharmacy, Complete Health, Evelyn Faye Nutrition, Family Pharmacy Granville, Discount Drug Stores, Amcal, My Chemist, Super Pharmacy, Simple Online Pharmacy, InstantScripts Shop, The Compounding Pharmacy, Kingsway Compounding, My Compounding Pharmacy, National Pharmacies, Blooms The Chemist, Priceline Pharmacy, Soul Pattinson Chemist, SuperPharmacyPlus, Your Discount Chemist, Doctors Own, Go Vita, A Vitamin Place, Australian Vitamins, The Healthy Place, Elite Supps, Bulk Nutrients, Amino Z, Mass Nutrition, ASN Online, Fit Supplements, Nutrition Capital, Vitamin Grocer AU.

---

## FOS Stock Report Matching

The app expects your FOS report to have these columns:
- `APN` (barcode)
- `Stock Name`, `Full Name`
- `Cost`, `Avg Cost`, `Sell Price`
- `SOH`, `Margin % (end date)`
- `Categories`, `Dept`
- `Qty Sold`, `Sales Val`

---

## License

MIT — use it, fork it, improve it.

Built for Burke Road Pharmacy / Z Office Sync.
