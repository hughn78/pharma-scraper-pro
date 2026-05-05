#!/usr/bin/env python3
"""
HTML Fallback Scraper for Non-Shopify Sites
============================================
Handles WooCommerce, BigCommerce, Magento, and generic e-commerce HTML.
Called from core.py when Shopify JSON probing fails.
"""

import re, json, time
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

def probe_and_scrape_html(sess, domain: str, batch_label: str,
                          db_conn, msg_queue=None, stop_event=None,
                          max_products: int = 500) -> Tuple[int, int]:
    """
    Attempt to scrape products from a non-Shopify site by walking
    category / collection pages and extracting product cards.
    Returns (products, variants).
    """
    base = f"https://{domain}"
    products_found = []

    # Try common category page patterns
    category_patterns = [
        "/collections/all",
        "/shop",
        "/products",
        "/vitamins-supplements",
        "/vitamins-and-supplements",
        "/health",
        "/supplements",
        "/categories/vitamins",
        "/product-category/vitamins",
        "/collections/vitamins",
    ]

    # First: try to find a sitemap or catalog page
    catalog_url = None
    for pattern in category_patterns:
        test_url = f"{base}{pattern}"
        try:
            r = sess.get(test_url, timeout=15, headers={"Accept": "text/html,*/*"})
            if r.status_code == 200 and len(r.text) > 5000:
                soup = BeautifulSoup(r.text, "html.parser")
                # Check if page has product cards
                product_links = soup.find_all("a", href=re.compile(r"/products?/[^/]+$"))
                if len(product_links) >= 3:
                    catalog_url = test_url
                    break
        except Exception:
            continue

    if not catalog_url:
        # Last resort: scrape homepage for product links
        try:
            r = sess.get(base, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                product_links = soup.find_all("a", href=re.compile(r"/products?/[^/]+$"))
                if len(product_links) >= 3:
                    catalog_url = base
                else:
                    return 0, 0
        except Exception:
            return 0, 0

    # Walk pagination if available
    page = 1
    visited_urls = set()
    while len(products_found) < max_products:
        if stop_event and stop_event.is_set():
            break

        page_url = f"{catalog_url}?page={page}" if page > 1 else catalog_url
        try:
            r = sess.get(page_url, timeout=20, headers={"Accept": "text/html,*/*"})
            if r.status_code != 200:
                break

            soup = BeautifulSoup(r.text, "html.parser")
            product_links = soup.find_all("a", href=re.compile(r"/products?/[^/]+$"))

            if not product_links:
                break

            for link in product_links:
                if stop_event and stop_event.is_set():
                    break
                href = link.get("href", "")
                if not href:
                    continue
                full_url = urljoin(base, href)
                if full_url in visited_urls:
                    continue
                visited_urls.add(full_url)

                # Scrape product page
                prod = scrape_product_page(sess, full_url, domain)
                if prod:
                    products_found.append(prod)

                if len(products_found) >= max_products:
                    break

                time.sleep(0.3)  # Be polite

            # Check for next page link
            next_link = soup.find("a", text=re.compile(r"next|»|›", re.I))
            if not next_link:
                next_link = soup.find("a", class_=re.compile(r"next|pagination"))
            if not next_link or page >= 20:
                break

            page += 1
            time.sleep(0.5)
        except Exception:
            break

    # Insert into database
    total = len(products_found)
    from core import SQL_UPSERT, log, close_conn, get_conn
    conn = db_conn or get_conn()
    c = conn.cursor()
    rows = []
    for p in products_found:
        rows.append((
            batch_label, p.get("source_name", domain.split(".")[0].title()), domain,
            p.get("name", ""), p.get("brand", ""), p.get("barcode", ""), p.get("sku", ""),
            p.get("size", ""), p.get("variant", ""), p.get("category", ""), "",
            p.get("description", ""), "", p.get("image", ""), p.get("url", ""),
            p.get("price", 0.0), 0.0, "AUD", "unknown",
            time.strftime("%Y-%m-%dT%H:%M:%S"), "html_fallback", json.dumps(p),
        ))
    if rows:
        try:
            c.executemany(SQL_UPSERT, rows)
            conn.commit()
        except Exception as e:
            log(f"[{domain}] HTML fallback DB error: {e}", "warning")
    if not db_conn:
        close_conn(conn)

    return total, total  # products, variants (1 variant per product for HTML)


def scrape_product_page(sess, url: str, domain: str) -> Optional[Dict[str, Any]]:
    """Scrape a single product page for basic data."""
    try:
        r = sess.get(url, timeout=15, headers={"Accept": "text/html,*/*"})
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # Title
        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).replace(f" | {domain}", "").split(" – ")[0].split(" - ")[0]
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

        # Price
        price = 0.0
        price_selectors = [
            "[data-price]", ".price", ".product-price", ".current-price", ".sale-price",
            '[class*="price"]', "meta[property='product:price:amount']",
            ".woocommerce-Price-amount", ".product-pricing"
        ]
        for sel in price_selectors:
            el = soup.select_one(sel)
            if el:
                price_text = el.get("content") or el.get_text(strip=True)
                price_match = re.search(r'[\d,]+\.\d{2}', price_text.replace(",", ""))
                if price_match:
                    try:
                        price = float(price_match.group())
                        break
                    except ValueError:
                        pass

        # Image
        image = ""
        img_selectors = [
            "meta[property='og:image']", "meta[property='product:image']",
            ".product-image img", "img[class*='product']", "[data-main-image]"
        ]
        for sel in img_selectors:
            el = soup.select_one(sel)
            if el:
                image = el.get("content") or el.get("src") or el.get("data-src") or ""
                if image:
                    break

        # Barcode / SKU from JSON-LD
        barcode = ""
        sku = ""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or script.get_text(" "))
                if isinstance(data, dict):
                    if data.get("@type") in ("Product", "IndividualProduct"):
                        barcode = str(data.get("gtin13") or data.get("gtin") or data.get("upc") or "")
                        sku = str(data.get("sku") or "")
                    # Some sites wrap in @graph
                    graph = data.get("@graph", [])
                    if isinstance(graph, list):
                        for node in graph:
                            if node.get("@type") == "Product":
                                barcode = str(node.get("gtin13") or node.get("gtin") or node.get("upc") or "")
                                sku = str(node.get("sku") or "")
                                break
            except Exception:
                pass

        # Brand
        brand = ""
        brand_selectors = [".brand", "[class*='brand']", "meta[property='product:brand']"]
        for sel in brand_selectors:
            el = soup.select_one(sel)
            if el:
                brand = el.get("content") or el.get_text(strip=True)
                break

        # Size extraction from title
        size = ""
        size_match = re.search(r'(\d+\s*(?:mg|g|kg|ml|mL|L|tablets?|capsules?|softgels?|tabs?|caps?))', title, re.I)
        if size_match:
            size = size_match.group(1).lower()

        # Description
        desc = ""
        desc_selectors = ["[class*='description']", "[class*='overview']", "#product-description"]
        for sel in desc_selectors:
            el = soup.select_one(sel)
            if el:
                desc = el.get_text(separator=" ", strip=True)[:500]
                break

        if not title or not price:
            return None

        return {
            "name": title,
            "brand": brand,
            "barcode": barcode,
            "sku": sku,
            "size": size,
            "variant": "",
            "category": "",
            "description": desc,
            "image": image,
            "url": url,
            "price": price,
            "source_name": domain.split(".")[0].title(),
        }

    except Exception:
        return None
