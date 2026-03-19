#!/usr/bin/env python3
"""
================================================================================
Sitemap Scraper v1.0
================================================================================
Scrapes sitemap URLs and extracts titles/meta descriptions for internal linking.

Usage:
    python3 sitemap-scraper.py --url https://example.com
    python3 sitemap-scraper.py --url https://example.com --output site-urls.json

Configuration:
    WP_URL in .env file is used as default if no --url provided
================================================================================
"""

import os
import sys
import re
import json
import argparse
import logging
import requests
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

# Load environment variables
try:
    from dotenv import load_dotenv
    script_dir = Path(__file__).resolve().parent
    env_file = script_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sitemap-scraper")

# Request settings
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SEO-Auditor/1.0; +https://example.com/bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 0.5  # Delay between requests to be polite


def fetch_sitemap_index(base_url: str) -> list:
    """Fetch sitemap index and return list of sitemap URLs."""
    sitemap_urls = []

    # Common sitemap locations
    sitemap_locations = [
        f"{base_url}/sitemap_index.xml",
        f"{base_url}/sitemap.xml",
        f"{base_url}/wp-sitemap.xml",
    ]

    for sitemap_url in sitemap_locations:
        try:
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                # Check if it's a sitemap index or direct sitemap
                if '<sitemapindex' in resp.text:
                    # Parse sitemap index
                    root = ET.fromstring(resp.content)
                    ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                    for sitemap in root.findall('.//ns:sitemap/ns:loc', ns):
                        sitemap_urls.append(sitemap.text)
                    log.info(f"Found sitemap index with {len(sitemap_urls)} sitemaps")
                    return sitemap_urls
                elif '<urlset' in resp.text:
                    # It's a direct sitemap
                    sitemap_urls.append(sitemap_url)
                    return sitemap_urls
        except Exception as e:
            log.debug(f"Failed to fetch {sitemap_url}: {e}")
            continue

    return sitemap_urls


def fetch_urls_from_sitemap(sitemap_url: str) -> list:
    """Fetch all URLs from a single sitemap."""
    urls = []

    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            root = ET.fromstring(resp.content)
            ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            for url in root.findall('.//ns:url/ns:loc', ns):
                urls.append(url.text)
    except Exception as e:
        log.error(f"Failed to parse sitemap {sitemap_url}: {e}")

    return urls


def scrape_page_meta(url: str) -> dict:
    """Scrape title and meta description from a URL."""
    result = {
        "url": url,
        "title": "",
        "meta_description": "",
        "h1": "",
        "type": "page",  # page, post, product, category
        "keywords": [],
    }

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return result

        soup = BeautifulSoup(resp.text, "lxml")

        # Get title
        title_tag = soup.find("title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)

        # Get meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            result["meta_description"] = meta_desc.get("content", "")

        # Get H1
        h1_tag = soup.find("h1")
        if h1_tag:
            result["h1"] = h1_tag.get_text(strip=True)

        # Detect page type from URL
        url_lower = url.lower()
        if "/blog/" in url_lower or "/post/" in url_lower:
            result["type"] = "post"
        elif "/product-category/" in url_lower or "/category/" in url_lower:
            result["type"] = "category"
        elif "/product/" in url_lower:
            result["type"] = "product"

        # Extract keywords from title and H1
        text = f"{result['title']} {result['h1']}".lower()
        # Remove common words and extract potential keywords
        words = re.findall(r'[а-яА-Яa-zA-Z]{4,}', text)
        result["keywords"] = list(set(words))[:10]

    except Exception as e:
        log.debug(f"Failed to scrape {url}: {e}")

    return result


def scrape_sitemap(base_url: str, output_file: str = None, max_urls: int = 500) -> dict:
    """Scrape all URLs from sitemap and extract metadata."""

    # Normalize base URL
    base_url = base_url.rstrip("/")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    domain = urlparse(base_url).netloc

    log.info(f"Scraping sitemap for: {base_url}")

    # Get all sitemap URLs
    sitemap_urls = fetch_sitemap_index(base_url)

    if not sitemap_urls:
        log.error("No sitemaps found")
        return {}

    # Collect all page URLs
    all_urls = []
    for sitemap_url in sitemap_urls:
        # Skip author and slider sitemaps
        if 'author' in sitemap_url.lower() or 'slider' in sitemap_url.lower():
            continue
        log.info(f"  Fetching: {sitemap_url}")
        urls = fetch_urls_from_sitemap(sitemap_url)
        all_urls.extend(urls)

    log.info(f"Found {len(all_urls)} URLs total")

    # Limit URLs if needed
    if len(all_urls) > max_urls:
        log.info(f"Limiting to {max_urls} URLs")
        # Prioritize: posts > categories > products > pages
        posts = [u for u in all_urls if '/blog/' in u.lower()]
        categories = [u for u in all_urls if '/product-category/' in u.lower() or '/category/' in u.lower()]
        products = [u for u in all_urls if '/product/' in u.lower()][:100]  # Limit products
        pages = [u for u in all_urls if u not in posts + categories + products][:50]

        all_urls = posts + categories + products + pages
        all_urls = all_urls[:max_urls]

    # Scrape each URL
    pages_data = []
    total = len(all_urls)

    for i, url in enumerate(all_urls, 1):
        log.info(f"  [{i}/{total}] Scraping: {url}")
        page_data = scrape_page_meta(url)
        if page_data["title"]:  # Only add if we got a title
            pages_data.append(page_data)
        time.sleep(REQUEST_DELAY)

    # Build result
    result = {
        "domain": domain,
        "base_url": base_url,
        "scraped_at": datetime.now().isoformat(),
        "total_urls": len(pages_data),
        "pages": pages_data,
        # Create quick lookup indexes
        "posts": [p for p in pages_data if p["type"] == "post"],
        "categories": [p for p in pages_data if p["type"] == "category"],
        "products": [p for p in pages_data if p["type"] == "product"],
    }

    # Save to file
    if output_file is None:
        output_file = Path(__file__).resolve().parent / f"site-urls-{domain.replace('.', '-')}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log.info(f"Saved {len(pages_data)} URLs to: {output_file}")
    log.info(f"  Posts: {len(result['posts'])}")
    log.info(f"  Categories: {len(result['categories'])}")
    log.info(f"  Products: {len(result['products'])}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Scrape sitemap URLs and extract metadata for internal linking"
    )
    parser.add_argument("-u", "--url", help="Website URL (default: WP_URL from .env)")
    parser.add_argument("-o", "--output", help="Output JSON file")
    parser.add_argument("-m", "--max", type=int, default=500, help="Max URLs to scrape (default: 500)")

    args = parser.parse_args()

    # Get URL from args or .env
    base_url = args.url or os.getenv("WP_URL", "")

    if not base_url:
        log.error("No URL provided. Use --url or set WP_URL in .env")
        sys.exit(1)

    scrape_sitemap(base_url, args.output, args.max)


if __name__ == "__main__":
    main()
