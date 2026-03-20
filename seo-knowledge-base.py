#!/usr/bin/env python3
"""
================================================================================
SEO Knowledge Base v1.0
================================================================================
Persistent storage for SEO data: sitemap URLs, SERP rankings, keywords,
competitor analysis. This data is used to inform AI-generated content.

Usage:
    # Update sitemap data
    python3 seo-knowledge-base.py --update-sitemap

    # Track keyword ranking
    python3 seo-knowledge-base.py --track-keyword "спално бельо памучен сатен"

    # Get context for AI
    python3 seo-knowledge-base.py --get-context "памучен сатен"

    # Show stats
    python3 seo-knowledge-base.py --stats

Configuration:
    WP_URL and SERPAPI_KEY in .env file
================================================================================
"""

import os
import sys
import json
import sqlite3
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
log = logging.getLogger("seo-kb")

# Configuration
CONFIG = {
    "wp_url": os.getenv("WP_URL", ""),
    "serpapi_key": os.getenv("SERPAPI_KEY", ""),
    "google_country": os.getenv("GOOGLE_COUNTRY", "bg"),
    "google_lang": os.getenv("GOOGLE_LANG", "bg"),
    "db_path": os.getenv("SEO_DB_PATH", str(script_dir / "seo-knowledge.db")),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SEO-Auditor/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class SEOKnowledgeBase:
    """Persistent storage for SEO data."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or CONFIG["db_path"]
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database with schema."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Site pages table (from sitemap)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                title TEXT,
                meta_description TEXT,
                h1 TEXT,
                page_type TEXT,
                word_count INTEGER,
                last_crawled TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Keywords table
        c.execute("""
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY,
                keyword TEXT UNIQUE NOT NULL,
                search_volume INTEGER,
                difficulty INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # SERP rankings history
        c.execute("""
            CREATE TABLE IF NOT EXISTS serp_rankings (
                id INTEGER PRIMARY KEY,
                keyword_id INTEGER,
                position INTEGER,
                url TEXT,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (keyword_id) REFERENCES keywords(id)
            )
        """)

        # Competitor pages
        c.execute("""
            CREATE TABLE IF NOT EXISTS competitors (
                id INTEGER PRIMARY KEY,
                domain TEXT NOT NULL,
                url TEXT,
                keyword_id INTEGER,
                title TEXT,
                meta_description TEXT,
                word_count INTEGER,
                position INTEGER,
                last_crawled TIMESTAMP,
                FOREIGN KEY (keyword_id) REFERENCES keywords(id)
            )
        """)

        # Page keywords (which keywords each page targets)
        c.execute("""
            CREATE TABLE IF NOT EXISTS page_keywords (
                page_id INTEGER,
                keyword_id INTEGER,
                keyword_count INTEGER,
                in_title BOOLEAN,
                in_h1 BOOLEAN,
                in_meta BOOLEAN,
                PRIMARY KEY (page_id, keyword_id),
                FOREIGN KEY (page_id) REFERENCES pages(id),
                FOREIGN KEY (keyword_id) REFERENCES keywords(id)
            )
        """)

        # Content suggestions (generated articles to avoid duplicates)
        c.execute("""
            CREATE TABLE IF NOT EXISTS content_suggestions (
                id INTEGER PRIMARY KEY,
                keyword TEXT,
                title TEXT,
                article_type TEXT,
                status TEXT DEFAULT 'suggested',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                published_at TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        log.debug(f"Database initialized: {self.db_path}")

    # =========================================================================
    # Sitemap Operations
    # =========================================================================

    def update_sitemap(self, base_url: str = None, max_urls: int = 500):
        """Fetch and store sitemap data."""
        base_url = base_url or CONFIG["wp_url"]
        if not base_url:
            log.error("No URL provided. Set WP_URL in .env or use --url")
            return

        base_url = base_url.rstrip("/")
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"

        log.info(f"Updating sitemap data for: {base_url}")

        # Fetch sitemap URLs
        all_urls = self._fetch_all_sitemap_urls(base_url)
        if not all_urls:
            log.error("No URLs found in sitemap")
            return

        log.info(f"Found {len(all_urls)} URLs in sitemap")

        # Limit and prioritize
        if len(all_urls) > max_urls:
            posts = [u for u in all_urls if '/blog/' in u.lower()]
            categories = [u for u in all_urls if '/product-category/' in u.lower() or '/category/' in u.lower()]
            products = [u for u in all_urls if '/product/' in u.lower()][:100]
            pages = [u for u in all_urls if u not in posts + categories + products][:50]
            all_urls = posts + categories + products + pages
            all_urls = all_urls[:max_urls]

        # Crawl and store each URL
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        for i, url in enumerate(all_urls, 1):
            log.info(f"  [{i}/{len(all_urls)}] {url}")
            page_data = self._scrape_page(url)

            c.execute("""
                INSERT OR REPLACE INTO pages
                (url, title, meta_description, h1, page_type, word_count, last_crawled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                url,
                page_data.get("title", ""),
                page_data.get("meta_description", ""),
                page_data.get("h1", ""),
                page_data.get("type", "page"),
                page_data.get("word_count", 0),
                datetime.now().isoformat()
            ))

            time.sleep(0.3)  # Be polite

        conn.commit()
        conn.close()
        log.info(f"Stored {len(all_urls)} pages in knowledge base")

    def _fetch_all_sitemap_urls(self, base_url: str) -> list:
        """Fetch all URLs from sitemap."""
        urls = []
        sitemap_locations = [
            f"{base_url}/sitemap_index.xml",
            f"{base_url}/sitemap.xml",
            f"{base_url}/wp-sitemap.xml",
        ]

        for sitemap_url in sitemap_locations:
            try:
                resp = requests.get(sitemap_url, headers=HEADERS, timeout=15)
                if resp.status_code != 200:
                    continue

                if '<sitemapindex' in resp.text:
                    # Parse sitemap index
                    root = ET.fromstring(resp.content)
                    ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                    for sitemap in root.findall('.//ns:sitemap/ns:loc', ns):
                        child_url = sitemap.text
                        if 'author' in child_url.lower() or 'slider' in child_url.lower():
                            continue
                        urls.extend(self._fetch_sitemap_urls(child_url))
                    break
                elif '<urlset' in resp.text:
                    urls = self._fetch_sitemap_urls(sitemap_url)
                    break
            except Exception as e:
                log.debug(f"Failed to fetch {sitemap_url}: {e}")

        return urls

    def _fetch_sitemap_urls(self, sitemap_url: str) -> list:
        """Fetch URLs from a single sitemap."""
        urls = []
        try:
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                for url in root.findall('.//ns:url/ns:loc', ns):
                    urls.append(url.text)
        except Exception as e:
            log.error(f"Failed to parse sitemap {sitemap_url}: {e}")
        return urls

    def _scrape_page(self, url: str) -> dict:
        """Scrape basic metadata from a page."""
        result = {"url": url, "type": "page", "word_count": 0}

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return result

            soup = BeautifulSoup(resp.text, "lxml")

            # Title
            title = soup.find("title")
            if title:
                result["title"] = title.get_text(strip=True)

            # Meta description
            meta = soup.find("meta", attrs={"name": "description"})
            if meta:
                result["meta_description"] = meta.get("content", "")

            # H1
            h1 = soup.find("h1")
            if h1:
                result["h1"] = h1.get_text(strip=True)

            # Word count
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            result["word_count"] = len(text.split())

            # Page type
            url_lower = url.lower()
            if "/blog/" in url_lower:
                result["type"] = "post"
            elif "/product-category/" in url_lower:
                result["type"] = "category"
            elif "/product/" in url_lower:
                result["type"] = "product"

        except Exception as e:
            log.debug(f"Failed to scrape {url}: {e}")

        return result

    # =========================================================================
    # Keyword Tracking
    # =========================================================================

    def track_keyword(self, keyword: str):
        """Track a keyword and check SERP ranking."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Add keyword if not exists
        c.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
        conn.commit()

        c.execute("SELECT id FROM keywords WHERE keyword = ?", (keyword,))
        keyword_id = c.fetchone()[0]

        # Check SERP ranking if SerpAPI is configured
        if CONFIG["serpapi_key"]:
            ranking = self._check_serp_ranking(keyword)
            if ranking:
                c.execute("""
                    INSERT INTO serp_rankings (keyword_id, position, url)
                    VALUES (?, ?, ?)
                """, (keyword_id, ranking.get("position"), ranking.get("url")))
                conn.commit()
                log.info(f"Keyword '{keyword}' ranking: #{ranking.get('position')} - {ranking.get('url')}")
        else:
            log.info(f"Keyword '{keyword}' added. Set SERPAPI_KEY to track rankings.")

        conn.close()

    def _check_serp_ranking(self, keyword: str) -> dict:
        """Check SERP ranking using SerpAPI."""
        try:
            params = {
                "q": keyword,
                "api_key": CONFIG["serpapi_key"],
                "engine": "google",
                "gl": CONFIG["google_country"],
                "hl": CONFIG["google_lang"],
                "num": 20,
            }
            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                domain = urlparse(CONFIG["wp_url"]).netloc.replace("www.", "")

                for i, result in enumerate(data.get("organic_results", []), 1):
                    if domain in result.get("link", ""):
                        return {"position": i, "url": result.get("link")}

                return {"position": 0, "url": None}  # Not ranking

        except Exception as e:
            log.error(f"SerpAPI error: {e}")

        return None

    # =========================================================================
    # Context for AI
    # =========================================================================

    def get_context_for_ai(self, keyword: str) -> dict:
        """Get relevant context for AI content generation."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        context = {
            "keyword": keyword,
            "existing_content": [],
            "related_pages": [],
            "serp_ranking": None,
            "competitors": [],
            "content_gaps": [],
        }

        keywords = keyword.lower().split()

        # Find existing content that covers this topic
        c.execute("""
            SELECT url, title, h1, page_type, word_count
            FROM pages
            WHERE title LIKE ? OR h1 LIKE ? OR meta_description LIKE ?
            ORDER BY word_count DESC
            LIMIT 10
        """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"))

        for row in c.fetchall():
            context["existing_content"].append(dict(row))

        # Find related pages (partial keyword matches)
        for kw in keywords:
            if len(kw) >= 4:
                c.execute("""
                    SELECT url, title, page_type
                    FROM pages
                    WHERE (title LIKE ? OR h1 LIKE ?)
                    AND url NOT IN (SELECT url FROM pages WHERE title LIKE ? OR h1 LIKE ?)
                    LIMIT 5
                """, (f"%{kw}%", f"%{kw}%", f"%{keyword}%", f"%{keyword}%"))

                for row in c.fetchall():
                    if dict(row) not in context["related_pages"]:
                        context["related_pages"].append(dict(row))

        # Get SERP ranking if tracked
        c.execute("""
            SELECT sr.position, sr.url, sr.checked_at
            FROM serp_rankings sr
            JOIN keywords k ON sr.keyword_id = k.id
            WHERE k.keyword = ?
            ORDER BY sr.checked_at DESC
            LIMIT 1
        """, (keyword,))

        row = c.fetchone()
        if row:
            context["serp_ranking"] = dict(row)

        # Get competitor data for this keyword
        c.execute("""
            SELECT domain, url, title, word_count, position
            FROM competitors
            WHERE keyword_id IN (SELECT id FROM keywords WHERE keyword = ?)
            ORDER BY position
            LIMIT 5
        """, (keyword,))

        for row in c.fetchall():
            context["competitors"].append(dict(row))

        # Get all pages for internal linking suggestions
        c.execute("""
            SELECT url, title, page_type
            FROM pages
            WHERE title IS NOT NULL AND title != ''
            ORDER BY
                CASE page_type
                    WHEN 'post' THEN 1
                    WHEN 'category' THEN 2
                    ELSE 3
                END,
                word_count DESC
            LIMIT 50
        """)

        context["available_pages"] = [dict(row) for row in c.fetchall()]

        conn.close()
        return context

    def format_context_for_prompt(self, keyword: str) -> str:
        """Format context as text for AI prompt."""
        ctx = self.get_context_for_ai(keyword)

        sections = []

        # Existing content warning
        if ctx["existing_content"]:
            sections.append("EXISTING CONTENT ON THIS TOPIC (avoid duplication):")
            for page in ctx["existing_content"][:5]:
                sections.append(f"  - {page['title']}: {page['url']}")

        # SERP ranking
        if ctx["serp_ranking"]:
            pos = ctx["serp_ranking"]["position"]
            if pos > 0:
                sections.append(f"\nCURRENT SERP RANKING: #{pos}")
            else:
                sections.append("\nCURRENT SERP RANKING: Not ranking")

        # Competitor data
        if ctx["competitors"]:
            sections.append("\nCOMPETITOR CONTENT:")
            for comp in ctx["competitors"]:
                sections.append(f"  #{comp['position']} {comp['domain']}: {comp['title']} ({comp['word_count']} words)")

        # Available pages for internal linking
        if ctx["available_pages"]:
            sections.append("\nAVAILABLE PAGES FOR INTERNAL LINKING:")
            for page in ctx["available_pages"][:15]:
                sections.append(f"  - [{page['page_type']}] {page['title']}: {page['url']}")

        return "\n".join(sections)

    # =========================================================================
    # Stats and Reporting
    # =========================================================================

    def get_stats(self) -> dict:
        """Get knowledge base statistics."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        stats = {}

        c.execute("SELECT COUNT(*) FROM pages")
        stats["total_pages"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM pages WHERE page_type = 'post'")
        stats["blog_posts"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM pages WHERE page_type = 'category'")
        stats["categories"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM pages WHERE page_type = 'product'")
        stats["products"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM keywords")
        stats["tracked_keywords"] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM serp_rankings")
        stats["serp_checks"] = c.fetchone()[0]

        c.execute("SELECT MAX(last_crawled) FROM pages")
        stats["last_sitemap_update"] = c.fetchone()[0]

        conn.close()
        return stats

    def print_stats(self):
        """Print knowledge base statistics."""
        stats = self.get_stats()
        print("\n" + "=" * 50)
        print("SEO KNOWLEDGE BASE STATS")
        print("=" * 50)
        print(f"  Total Pages:       {stats['total_pages']}")
        print(f"    - Blog Posts:    {stats['blog_posts']}")
        print(f"    - Categories:    {stats['categories']}")
        print(f"    - Products:      {stats['products']}")
        print(f"  Tracked Keywords:  {stats['tracked_keywords']}")
        print(f"  SERP Checks:       {stats['serp_checks']}")
        print(f"  Last Updated:      {stats['last_sitemap_update'] or 'Never'}")
        print("=" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="SEO Knowledge Base - Persistent storage for SEO data"
    )
    parser.add_argument("--update-sitemap", action="store_true", help="Update sitemap data")
    parser.add_argument("--url", help="Website URL (default: WP_URL from .env)")
    parser.add_argument("--track-keyword", metavar="KW", help="Track a keyword ranking")
    parser.add_argument("--get-context", metavar="KW", help="Get AI context for keyword")
    parser.add_argument("--stats", action="store_true", help="Show knowledge base stats")
    parser.add_argument("--max", type=int, default=500, help="Max URLs to crawl")

    args = parser.parse_args()

    kb = SEOKnowledgeBase()

    if args.update_sitemap:
        kb.update_sitemap(args.url, args.max)
    elif args.track_keyword:
        kb.track_keyword(args.track_keyword)
    elif args.get_context:
        context = kb.format_context_for_prompt(args.get_context)
        print(context)
    elif args.stats:
        kb.print_stats()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
