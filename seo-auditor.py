#!/usr/bin/env python3
"""
============================================================================
SEO Competitive Auditor v1.1
============================================================================
Searches Google for a keyword, finds competitors outranking your page,
crawls both your page and theirs, runs comprehensive SEO analysis,
and uses AI (Claude or OpenAI) to generate actionable improvement suggestions.

Usage:
    python3 seo-auditor.py --keyword "oak flooring uk" --domain flooringsuppliescentre.co.uk
    python3 seo-auditor.py --keyword "engineered wood flooring" --domain oakparquetflooring.co.uk --provider openai
    python3 seo-auditor.py --keyword "laminate flooring" --domain tradescentre.co.uk --provider claude --top 5

Requirements:
    pip install requests beautifulsoup4 lxml googlesearch-python python-dotenv
    pip install anthropic          (if using --provider claude)
    pip install openai             (if using --provider openai)

Configuration:
    Copy .env.example to .env and fill in your values.
    All settings can be configured via environment variables.
    See .env.example for the full list of options.
============================================================================
"""

import os
import sys
import re
import json
import csv
import time
import hashlib
import argparse
import smtplib
import logging
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dataclasses import dataclass, field, asdict
from typing import Optional
from textwrap import dedent
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Look for .env in the script directory first, then current directory
    script_dir = Path(__file__).resolve().parent
    env_file = script_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()  # Try current directory
except ImportError:
    pass  # python-dotenv not installed, rely on system env vars

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _parse_emails(env_val: str) -> list:
    """Parse comma-separated email list from env var."""
    if not env_val:
        return []
    return [e.strip() for e in env_val.split(",") if e.strip()]


DEFAULT_CONFIG = {
    # Audit settings
    "top_competitors": int(os.getenv("TOP_COMPETITORS", "5")),
    "request_timeout": int(os.getenv("REQUEST_TIMEOUT", "15")),
    "request_delay": int(os.getenv("REQUEST_DELAY", "2")),

    # Google search settings
    "google_country": os.getenv("GOOGLE_COUNTRY", "co.uk"),
    "google_lang": os.getenv("GOOGLE_LANG", "en"),
    "google_num_results": int(os.getenv("GOOGLE_NUM_RESULTS", "10")),

    # Content analysis thresholds
    "min_content_words": int(os.getenv("MIN_CONTENT_WORDS", "300")),
    "max_title_length": int(os.getenv("MAX_TITLE_LENGTH", "60")),
    "max_meta_desc_length": int(os.getenv("MAX_META_DESC_LENGTH", "160")),
    "min_meta_desc_length": int(os.getenv("MIN_META_DESC_LENGTH", "50")),

    # Output settings
    "report_dir": os.getenv("REPORT_DIR", "/tmp/seo-reports"),

    # Email settings
    "alert_emails": _parse_emails(os.getenv("ALERT_EMAILS", "")),
    "smtp_host": os.getenv("SMTP_HOST", "localhost"),
    "smtp_port": int(os.getenv("SMTP_PORT", "25")),
    "smtp_user": os.getenv("SMTP_USER", ""),
    "smtp_pass": os.getenv("SMTP_PASS", ""),
    # Encryption: "none", "starttls" (port 587), or "ssl" (port 465)
    "smtp_encryption": os.getenv("SMTP_ENCRYPTION", "none").lower(),

    # AI provider settings
    "ai_provider": os.getenv("AI_PROVIDER", "auto"),
    "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "openai_model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
    "claude_model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),

    # Search API settings
    "serpapi_key": os.getenv("SERPAPI_KEY", ""),

    # Manual competitors (used when SERPAPI_KEY is not set)
    "competitors": _parse_emails(os.getenv("COMPETITORS", "")),  # reuse comma parser
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seo-auditor")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SEOIssue:
    severity: str        # critical, warning, info
    category: str        # title, meta, content, images, links, technical, structured_data
    message: str
    suggestion: str = ""

@dataclass
class PageAnalysis:
    url: str
    domain: str
    status_code: int = 0
    load_time: float = 0.0
    title: str = ""
    meta_description: str = ""
    canonical: str = ""
    h1_tags: list = field(default_factory=list)
    h2_tags: list = field(default_factory=list)
    h3_tags: list = field(default_factory=list)
    word_count: int = 0
    content_text: str = ""
    images_total: int = 0
    images_missing_alt: int = 0
    images_missing_alt_list: list = field(default_factory=list)
    internal_links: int = 0
    external_links: int = 0
    broken_links: list = field(default_factory=list)
    has_og_tags: bool = False
    has_twitter_cards: bool = False
    has_schema_markup: bool = False
    schema_types: list = field(default_factory=list)
    has_robots_meta: str = ""
    has_viewport: bool = False
    has_hreflang: bool = False
    has_sitemap_ref: bool = False
    page_size_kb: float = 0.0
    issues: list = field(default_factory=list)
    serp_position: int = 0
    is_own_page: bool = False

@dataclass
class SERPResult:
    position: int
    url: str
    domain: str
    title: str = ""
    snippet: str = ""


# ---------------------------------------------------------------------------
# SERP Checker — Google search
# ---------------------------------------------------------------------------

class SERPChecker:
    """Fetch Google SERP results for a keyword."""

    def __init__(self, config: dict):
        self.config = config

    def search(self, keyword: str, own_domain: str) -> tuple[list[SERPResult], Optional[SERPResult]]:
        """
        Search Google for keyword, return (all_results, own_result).
        own_result is None if our domain doesn't rank in the results.
        Tries SerpAPI first, then googlesearch-python, then direct scraping.
        """
        log.info(f"Searching Google (.{self.config['google_country']}) for: '{keyword}'")
        results = []
        own_result = None

        # Try SerpAPI first (most reliable)
        serpapi_key = self.config.get("serpapi_key", "")
        if serpapi_key:
            results, own_result = self._serpapi_search(keyword, own_domain, serpapi_key)
            if results:
                return results, own_result
            log.warning("SerpAPI returned no results, trying fallback...")

        # Try googlesearch-python
        try:
            from googlesearch import search as gsearch
            urls = list(gsearch(
                keyword,
                num_results=self.config["google_num_results"],
                lang=self.config["google_lang"],
                region=self.config["google_country"].replace("co.", ""),
            ))

            for i, url in enumerate(urls, 1):
                domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
                sr = SERPResult(position=i, url=url, domain=domain)
                results.append(sr)
                if own_domain.replace("www.", "") in domain:
                    own_result = sr

        except ImportError:
            log.warning("googlesearch-python not installed, falling back to manual SERP fetch")
            results, own_result = self._fallback_search(keyword, own_domain)
        except Exception as e:
            log.error(f"Google search failed: {e}")
            results, own_result = self._fallback_search(keyword, own_domain)

        log.info(f"Found {len(results)} SERP results")
        if own_result:
            log.info(f"Your domain ranks at position #{own_result.position}")
        else:
            log.warning(f"Your domain '{own_domain}' was NOT found in top {len(results)} results")

        return results, own_result

    def _serpapi_search(self, keyword: str, own_domain: str, api_key: str) -> tuple[list, Optional[SERPResult]]:
        """Search using SerpAPI (https://serpapi.com)."""
        results = []
        own_result = None

        try:
            # Map country codes to Google domains
            country = self.config["google_country"]
            gl_map = {"co.uk": "uk", "bg": "bg", "de": "de", "fr": "fr", "com": "us"}
            gl = gl_map.get(country, country.replace("co.", ""))

            params = {
                "q": keyword,
                "api_key": api_key,
                "engine": "google",
                "num": self.config["google_num_results"],
                "hl": self.config["google_lang"],
                "gl": gl,
                "google_domain": f"google.{country}",
            }

            log.info(f"Using SerpAPI (google.{country}, gl={gl})...")
            resp = requests.get("https://serpapi.com/search", params=params, timeout=30)
            data = resp.json()

            if "error" in data:
                log.error(f"SerpAPI error: {data['error']}")
                return [], None

            organic = data.get("organic_results", [])
            for i, item in enumerate(organic, 1):
                url = item.get("link", "")
                domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
                sr = SERPResult(
                    position=i,
                    url=url,
                    domain=domain,
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                )
                results.append(sr)
                if own_domain.replace("www.", "") in domain:
                    own_result = sr

            log.info(f"SerpAPI returned {len(results)} results")

        except Exception as e:
            log.error(f"SerpAPI request failed: {e}")

        return results, own_result

    def _fallback_search(self, keyword: str, own_domain: str) -> tuple[list, Optional[SERPResult]]:
        """Fallback: scrape Google directly (less reliable)."""
        results = []
        own_result = None
        try:
            params = {
                "q": keyword,
                "num": self.config["google_num_results"],
                "hl": self.config["google_lang"],
            }
            url = f"https://www.google.{self.config['google_country']}/search"
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=10)
            soup = BeautifulSoup(resp.text, "lxml")

            position = 0
            for div in soup.select("div.tF2Cxc, div.g"):
                link = div.select_one("a[href]")
                if not link:
                    continue
                href = link["href"]
                if not href.startswith("http"):
                    continue
                position += 1
                domain = urllib.parse.urlparse(href).netloc.replace("www.", "")
                title_el = div.select_one("h3")
                snippet_el = div.select_one("div.VwiC3b, span.aCOpRe")
                sr = SERPResult(
                    position=position,
                    url=href,
                    domain=domain,
                    title=title_el.get_text(strip=True) if title_el else "",
                    snippet=snippet_el.get_text(strip=True) if snippet_el else "",
                )
                results.append(sr)
                if own_domain.replace("www.", "") in domain:
                    own_result = sr
        except Exception as e:
            log.error(f"Fallback search also failed: {e}")

        return results, own_result


# ---------------------------------------------------------------------------
# Page Crawler & Analyser
# ---------------------------------------------------------------------------

class PageAnalyser:
    """Crawl a single page and perform comprehensive SEO analysis."""

    def __init__(self, config: dict):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def find_best_page_for_keyword(self, base_url: str, keyword: str) -> str:
        """
        Find the most relevant page for a keyword on a competitor site.
        1. Crawl homepage and extract all internal links
        2. Score links based on keyword relevance in URL and anchor text
        3. Return the best matching URL, or homepage if nothing better found
        """
        log.info(f"    Searching for best page matching '{keyword}'...")

        try:
            resp = self.session.get(base_url, timeout=self.config["request_timeout"])
            if resp.status_code != 200:
                return base_url

            soup = BeautifulSoup(resp.text, "lxml")
            parsed_base = urllib.parse.urlparse(base_url)
            base_domain = parsed_base.netloc.replace("www.", "")

            # Normalize keyword for matching
            keyword_lower = keyword.lower()
            keyword_parts = keyword_lower.split()

            # Transliterate Bulgarian to Latin for URL matching
            translit_map = {
                'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'zh',
                'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
                'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
                'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sht', 'ъ': 'a', 'ь': '',
                'ю': 'yu', 'я': 'ya', ' ': '-'
            }
            keyword_translit = ''.join(translit_map.get(c, c) for c in keyword_lower)
            keyword_translit_alt = keyword_translit.replace('-', '')

            # Common URL patterns to try
            url_patterns = [
                keyword_translit,           # spalno-belyo
                keyword_translit_alt,       # spalnobelyo
                keyword_translit.replace('-', '_'),  # spalno_belyo
            ]

            candidates = []

            # Check all links on the page
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                    continue

                full_url = urllib.parse.urljoin(base_url, href)
                link_domain = urllib.parse.urlparse(full_url).netloc.replace("www.", "")

                # Only internal links
                if base_domain not in link_domain:
                    continue

                url_path = urllib.parse.urlparse(full_url).path.lower()
                anchor_text = a.get_text(strip=True).lower()

                score = 0

                # Score based on URL path matching
                for pattern in url_patterns:
                    if pattern in url_path:
                        score += 50
                        break

                # Score based on anchor text containing keyword
                if keyword_lower in anchor_text:
                    score += 40

                # Partial keyword matches
                for part in keyword_parts:
                    if len(part) > 2:
                        if part in url_path:
                            score += 10
                        if part in anchor_text:
                            score += 10

                # Prefer category/collection pages
                if any(x in url_path for x in ['/category/', '/categories/', '/collection/', '/product-category/', '/katalog/', '/produkti/']):
                    score += 15

                # Penalize very long URLs (likely product pages)
                if url_path.count('/') > 4:
                    score -= 10

                # Penalize common non-relevant pages
                if any(x in url_path for x in ['/cart', '/checkout', '/account', '/login', '/contact', '/about', '/blog/', '/news/']):
                    score -= 30

                if score > 0:
                    candidates.append((score, full_url, anchor_text[:50]))

            # Sort by score and return best match
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                best_score, best_url, best_anchor = candidates[0]
                if best_score >= 20:  # Minimum threshold
                    log.info(f"    Found relevant page (score={best_score}): {best_url}")
                    return best_url

            log.info(f"    No specific page found, using homepage")
            return base_url

        except Exception as e:
            log.warning(f"    Error finding best page: {e}")
            return base_url

    def analyse(self, url: str, is_own: bool = False) -> PageAnalysis:
        """Fetch and analyse a single URL."""
        domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
        page = PageAnalysis(url=url, domain=domain, is_own_page=is_own)

        log.info(f"  Crawling: {url}")
        try:
            start = time.time()
            resp = self.session.get(url, timeout=self.config["request_timeout"], allow_redirects=True)
            page.load_time = round(time.time() - start, 2)
            page.status_code = resp.status_code
            page.page_size_kb = round(len(resp.content) / 1024, 1)

            if resp.status_code != 200:
                page.issues.append(SEOIssue(
                    "critical", "technical",
                    f"Page returned HTTP {resp.status_code}",
                    "Ensure the page returns a 200 status code."
                ))
                return page

            soup = BeautifulSoup(resp.text, "lxml")
            self._extract_metadata(soup, page)
            self._extract_headings(soup, page)
            self._extract_content(soup, page)
            self._extract_images(soup, page, url)
            self._extract_links(soup, page, url)
            self._extract_technical(soup, page)
            self._extract_structured_data(soup, resp.text, page)

            if is_own:
                self._run_seo_checks(page)

        except requests.Timeout:
            page.issues.append(SEOIssue(
                "critical", "technical",
                f"Page timed out after {self.config['request_timeout']}s",
                "Check server performance — slow pages hurt rankings."
            ))
        except Exception as e:
            page.issues.append(SEOIssue(
                "critical", "technical",
                f"Failed to crawl: {str(e)[:200]}",
            ))

        return page

    def _extract_metadata(self, soup: BeautifulSoup, page: PageAnalysis):
        """Extract title, meta description, canonical, OG, Twitter."""
        title_tag = soup.find("title")
        page.title = title_tag.get_text(strip=True) if title_tag else ""

        meta_desc = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
        page.meta_description = meta_desc.get("content", "").strip() if meta_desc else ""

        canonical = soup.find("link", attrs={"rel": "canonical"})
        page.canonical = canonical.get("href", "").strip() if canonical else ""

        # Open Graph
        page.has_og_tags = bool(soup.find("meta", attrs={"property": re.compile(r"^og:")}))

        # Twitter Cards
        page.has_twitter_cards = bool(soup.find("meta", attrs={"name": re.compile(r"^twitter:")}))

        # Robots meta
        robots = soup.find("meta", attrs={"name": re.compile(r"robots", re.I)})
        page.has_robots_meta = robots.get("content", "").strip() if robots else ""

        # Viewport
        page.has_viewport = bool(soup.find("meta", attrs={"name": "viewport"}))

        # Hreflang
        page.has_hreflang = bool(soup.find("link", attrs={"rel": "alternate", "hreflang": True}))

    def _extract_headings(self, soup: BeautifulSoup, page: PageAnalysis):
        """Extract H1, H2, H3 tags."""
        page.h1_tags = [h.get_text(strip=True) for h in soup.find_all("h1")]
        page.h2_tags = [h.get_text(strip=True) for h in soup.find_all("h2")]
        page.h3_tags = [h.get_text(strip=True) for h in soup.find_all("h3")]

    def _extract_content(self, soup: BeautifulSoup, page: PageAnalysis):
        """Extract main content text and word count."""
        # Remove script, style, nav, footer, header for content analysis
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        # Remove HTML comments
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        # Try to find main content area
        main = soup.find("main") or soup.find("article") or soup.find("div", class_=re.compile(r"content|entry|post", re.I))
        content_el = main if main else soup.find("body")

        if content_el:
            text = content_el.get_text(separator=" ", strip=True)
            # Clean up whitespace
            text = re.sub(r"\s+", " ", text).strip()
            page.content_text = text
            page.word_count = len(text.split())

    def _extract_images(self, soup_original: BeautifulSoup, page: PageAnalysis, base_url: str):
        """Check images for alt text."""
        # Re-parse from original since we decomposed tags above
        # We'll count from the page data we already have
        # Actually we need a fresh soup for images — let's use a workaround
        # by counting from the initial page analysis pass
        # For now, reparse is simplest
        pass

    def _extract_links(self, soup: BeautifulSoup, page: PageAnalysis, base_url: str):
        """Count internal/external links, check for broken ones."""
        parsed_base = urllib.parse.urlparse(base_url)
        base_domain = parsed_base.netloc.replace("www.", "")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue
            full_url = urllib.parse.urljoin(base_url, href)
            link_domain = urllib.parse.urlparse(full_url).netloc.replace("www.", "")

            if base_domain in link_domain:
                page.internal_links += 1
            else:
                page.external_links += 1

    def _extract_technical(self, soup: BeautifulSoup, page: PageAnalysis):
        """Extract technical SEO signals."""
        pass  # viewport, hreflang etc already handled in _extract_metadata

    def _extract_structured_data(self, soup: BeautifulSoup, html: str, page: PageAnalysis):
        """Check for JSON-LD and microdata schema markup."""
        # JSON-LD
        json_ld_scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and "@type" in data:
                    page.schema_types.append(data["@type"])
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "@type" in item:
                            page.schema_types.append(item["@type"])
            except (json.JSONDecodeError, TypeError):
                pass

        page.has_schema_markup = len(page.schema_types) > 0

    def _run_seo_checks(self, page: PageAnalysis):
        """Run all SEO checks on our own page and generate issues."""
        cfg = self.config

        # --- Title ---
        if not page.title:
            page.issues.append(SEOIssue(
                "critical", "title", "Missing page title",
                "Add a unique, keyword-rich <title> tag."
            ))
        elif len(page.title) > cfg["max_title_length"]:
            page.issues.append(SEOIssue(
                "warning", "title",
                f"Title too long ({len(page.title)} chars, max {cfg['max_title_length']})",
                f"Shorten to under {cfg['max_title_length']} characters to avoid truncation in SERPs."
            ))
        elif len(page.title) < 20:
            page.issues.append(SEOIssue(
                "warning", "title",
                f"Title too short ({len(page.title)} chars)",
                "Expand with relevant keywords and value proposition."
            ))

        # --- Meta Description ---
        if not page.meta_description:
            page.issues.append(SEOIssue(
                "critical", "meta", "Missing meta description",
                "Add a compelling meta description with your target keyword."
            ))
        elif len(page.meta_description) > cfg["max_meta_desc_length"]:
            page.issues.append(SEOIssue(
                "warning", "meta",
                f"Meta description too long ({len(page.meta_description)} chars, max {cfg['max_meta_desc_length']})",
                "Shorten to avoid truncation in search results."
            ))
        elif len(page.meta_description) < cfg["min_meta_desc_length"]:
            page.issues.append(SEOIssue(
                "warning", "meta",
                f"Meta description too short ({len(page.meta_description)} chars)",
                "Expand to better describe the page and include a call to action."
            ))

        # --- H1 Tags ---
        if not page.h1_tags:
            page.issues.append(SEOIssue(
                "critical", "content", "Missing H1 tag",
                "Add exactly one H1 tag containing your primary keyword."
            ))
        elif len(page.h1_tags) > 1:
            page.issues.append(SEOIssue(
                "warning", "content",
                f"Multiple H1 tags ({len(page.h1_tags)}): {', '.join(page.h1_tags[:3])}",
                "Use only one H1 per page for clear content hierarchy."
            ))

        # --- Content / Word Count ---
        if page.word_count < cfg["min_content_words"]:
            page.issues.append(SEOIssue(
                "warning", "content",
                f"Thin content ({page.word_count} words, minimum recommended: {cfg['min_content_words']})",
                "Add more valuable, relevant content to improve rankings."
            ))

        # --- Heading hierarchy ---
        if page.h1_tags and not page.h2_tags:
            page.issues.append(SEOIssue(
                "info", "content",
                "No H2 tags found — flat content structure",
                "Break content into sections with H2 subheadings for better readability and SEO."
            ))

        # --- Images ---
        if page.images_missing_alt > 0:
            page.issues.append(SEOIssue(
                "warning", "images",
                f"{page.images_missing_alt} images missing alt text",
                "Add descriptive alt text to all images for accessibility and image SEO."
            ))

        # --- Canonical ---
        if not page.canonical:
            page.issues.append(SEOIssue(
                "warning", "technical",
                "Missing canonical tag",
                "Add a canonical URL to prevent duplicate content issues."
            ))

        # --- Open Graph ---
        if not page.has_og_tags:
            page.issues.append(SEOIssue(
                "info", "technical",
                "Missing Open Graph tags",
                "Add OG tags (og:title, og:description, og:image) for better social sharing."
            ))

        # --- Schema Markup ---
        if not page.has_schema_markup:
            page.issues.append(SEOIssue(
                "info", "structured_data",
                "No structured data (schema.org) found",
                "Add JSON-LD schema markup (Product, LocalBusiness, FAQ, etc.) for rich snippets."
            ))

        # --- Viewport ---
        if not page.has_viewport:
            page.issues.append(SEOIssue(
                "critical", "technical",
                "Missing viewport meta tag",
                "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> for mobile."
            ))

        # --- Page Speed ---
        if page.load_time > 3.0:
            page.issues.append(SEOIssue(
                "warning", "technical",
                f"Slow page load: {page.load_time}s (server response, not full render)",
                "Optimise images, enable caching, check PHP-FPM/MySQL performance."
            ))

        # --- Page Size ---
        if page.page_size_kb > 3000:
            page.issues.append(SEOIssue(
                "warning", "technical",
                f"Large page size: {page.page_size_kb}KB",
                "Consider compressing images, minifying CSS/JS, enabling gzip."
            ))

        # --- Noindex check ---
        if "noindex" in page.has_robots_meta.lower():
            page.issues.append(SEOIssue(
                "critical", "technical",
                "Page has noindex directive — it will NOT appear in search results",
                "Remove noindex if this page should be indexed."
            ))


# ---------------------------------------------------------------------------
# Image analysis helper (needs fresh soup)
# ---------------------------------------------------------------------------

def analyse_images_from_html(html: str, base_url: str, page: PageAnalysis):
    """Separate pass to count images and missing alt tags from raw HTML."""
    soup = BeautifulSoup(html, "lxml")
    images = soup.find_all("img")
    page.images_total = len(images)
    for img in images:
        alt = img.get("alt", "").strip()
        src = img.get("src", img.get("data-src", ""))
        if not alt:
            page.images_missing_alt += 1
            if src:
                page.images_missing_alt_list.append(src[:120])


# ---------------------------------------------------------------------------
# AI Content Advisor (Claude or OpenAI)
# ---------------------------------------------------------------------------

class AIAdvisor:
    """Use Claude or OpenAI to generate content improvement suggestions."""

    def __init__(self, config: dict):
        self.config = config
        self.provider = config.get("ai_provider", "auto")
        self.anthropic_key = config.get("anthropic_api_key", "")
        self.openai_key = config.get("openai_api_key", "")

        # Auto-detect provider based on available keys
        if self.provider == "auto":
            if self.anthropic_key:
                self.provider = "claude"
            elif self.openai_key:
                self.provider = "openai"
            else:
                self.provider = "none"

        # Validate chosen provider has a key
        if self.provider == "claude" and not self.anthropic_key:
            log.warning("--provider claude but ANTHROPIC_API_KEY not set")
            self.provider = "none"
        elif self.provider == "openai" and not self.openai_key:
            log.warning("--provider openai but OPENAI_API_KEY not set")
            self.provider = "none"

        self.enabled = self.provider != "none"
        if not self.enabled:
            log.warning("No AI API key found — AI suggestions will be skipped")
            log.warning("Set ANTHROPIC_API_KEY or OPENAI_API_KEY, or use --no-ai to silence this")
        else:
            log.info(f"AI provider: {self.provider}")

    def _build_prompt(self, keyword: str, own_page, competitor_pages: list) -> str:
        """Build the SEO analysis prompt (shared by all providers)."""

        # Build keyword analysis for own page
        own_keyword_analysis = f"""
            Keyword "{keyword}" Analysis:
            - In Title: {'YES' if getattr(own_page, 'keyword_in_title', False) else 'NO'}
            - In H1: {'YES' if getattr(own_page, 'keyword_in_h1', False) else 'NO'}
            - In H2s: {'YES' if getattr(own_page, 'keyword_in_h2', False) else 'NO'}
            - In Meta Description: {'YES' if getattr(own_page, 'keyword_in_meta', False) else 'NO'}
            - Occurrences in Content: {getattr(own_page, 'keyword_count', 0)}"""

        # Build competitor summaries with keyword analysis
        comp_summaries = []
        for cp in competitor_pages[:5]:
            keyword_info = f"""
            Keyword "{keyword}" Analysis:
            - In Title: {'YES' if getattr(cp, 'keyword_in_title', False) else 'NO'}
            - In H1: {'YES' if getattr(cp, 'keyword_in_h1', False) else 'NO'}
            - In H2s: {'YES' if getattr(cp, 'keyword_in_h2', False) else 'NO'}
            - Occurrences in Content: {getattr(cp, 'keyword_count', 0)}"""

            comp_summaries.append(dedent(f"""\
                --- Competitor #{cp.serp_position}: {cp.domain} ---
                URL: {cp.url}
                Title: {cp.title}
                Meta Description: {cp.meta_description}
                H1: {', '.join(cp.h1_tags[:3])}
                H2s: {', '.join(cp.h2_tags[:10])}
                Word Count: {cp.word_count}
                Schema Types: {', '.join(cp.schema_types) or 'None'}
                {keyword_info}
                Content Preview (first 1500 chars):
                {cp.content_text[:1500]}
            """))

        return dedent(f"""\
            You are an expert SEO consultant. Analyse the following data and provide
            a WINNING STRATEGY to help my page outrank the competitors for the keyword: "{keyword}"

            === MY PAGE ===
            URL: {own_page.url}
            SERP Position: #{own_page.serp_position if own_page.serp_position else 'Not ranking'}
            Title: {own_page.title}
            Meta Description: {own_page.meta_description}
            H1: {', '.join(own_page.h1_tags[:3])}
            H2s: {', '.join(own_page.h2_tags[:10])}
            Word Count: {own_page.word_count}
            Schema Types: {', '.join(own_page.schema_types) or 'None'}
            {own_keyword_analysis}
            Content Preview (first 2000 chars):
            {own_page.content_text[:2000]}

            === COMPETITORS ===
            {chr(10).join(comp_summaries)}

            Please provide your response in the following HTML structure (no markdown):

            1. <h3>Keyword Usage Comparison</h3> — compare how competitors use the keyword
               vs my page. Who uses it best? Where am I falling short?

            2. <h3>Title & Meta Description Improvements</h3> — suggest better title and
               meta description that include the keyword naturally and improve CTR.

            3. <h3>Content Gap Analysis</h3> — what topics/sections do competitors cover
               that my page is missing? What makes their content rank better?

            4. <h3>Content Structure Recommendations</h3> — suggest specific H2/H3
               subheadings I should add, incorporating the keyword where natural.

            5. <h3>Schema Markup Suggestions</h3> — what structured data do competitors
               use? What should I add?

            6. <h3>WINNING STRATEGY</h3> — based on your analysis, provide a step-by-step
               action plan to outrank these competitors. Be specific about:
               - Content improvements needed
               - Keyword placement optimization
               - Technical SEO fixes
               - Unique angles or content that could differentiate my page

            7. <h3>Quick Wins</h3> — 5 easy changes I can implement TODAY for immediate impact.

            Keep suggestions specific and actionable. Reference actual content from competitors.
            Format as clean HTML with <ul>, <li>, <p>, <strong> tags.
        """)

    def _call_claude(self, prompt: str) -> str:
        """Call Anthropic Claude API."""
        try:
            import anthropic
        except ImportError:
            return "<p><em>anthropic package not installed. Run: pip install anthropic</em></p>"

        try:
            client = anthropic.Anthropic(api_key=self.anthropic_key)
            model = self.config.get("claude_model", "claude-sonnet-4-20250514")
            log.info(f"Asking Claude ({model}) for content suggestions...")
            response = client.messages.create(
                model=model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            log.error(f"Claude API error: {e}")
            return f"<p><em>Claude API error: {e}</em></p>"

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API."""
        try:
            import openai
        except ImportError:
            return "<p><em>openai package not installed. Run: pip install openai</em></p>"

        try:
            client = openai.OpenAI(api_key=self.openai_key)
            model = self.config.get("openai_model", "gpt-4.1-mini")
            log.info(f"Asking OpenAI ({model}) for content suggestions...")
            response = client.chat.completions.create(
                model=model,
                max_tokens=4000,
                messages=[
                    {"role": "system", "content": "You are an expert SEO consultant. Respond only in clean HTML (no markdown)."},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            log.error(f"OpenAI API error: {e}")
            return f"<p><em>OpenAI API error: {e}</em></p>"

    def compare_and_suggest(
        self, keyword: str, own_page,
        competitor_pages: list, crawl_data: dict = None
    ) -> str:
        """Compare our page vs competitors and generate AI suggestions."""
        if not self.enabled:
            return "<p><em>AI suggestions unavailable — set ANTHROPIC_API_KEY or OPENAI_API_KEY.</em></p>"

        if competitor_pages and crawl_data:
            prompt = self._build_full_analysis_prompt(keyword, crawl_data)
        elif competitor_pages:
            prompt = self._build_prompt(keyword, own_page, competitor_pages)
        else:
            prompt = self._build_audit_only_prompt(keyword, own_page)

        if self.provider == "claude":
            return self._call_claude(prompt)
        elif self.provider == "openai":
            return self._call_openai(prompt)
        else:
            return "<p><em>Unknown AI provider configured.</em></p>"

    def _build_full_analysis_prompt(self, keyword: str, data: dict) -> str:
        """Build comprehensive prompt using all crawled data."""
        own = data.get('own_page', {})
        competitors = data.get('competitors', [])

        # Format own page data
        own_section = f"""
URL: {own.get('url', 'N/A')}
Title: "{own.get('title', {}).get('text', '')}" ({own.get('title', {}).get('length', 0)} chars, keyword: {'YES' if own.get('title', {}).get('has_keyword') else 'NO'})
Meta Description: "{own.get('meta_description', {}).get('text', '')}" ({own.get('meta_description', {}).get('length', 0)} chars, keyword: {'YES' if own.get('meta_description', {}).get('has_keyword') else 'NO'})
Canonical: {own.get('canonical', 'Not set')}

HEADINGS:
  H1 Tags ({len(own.get('headings', {}).get('h1', []))}): {', '.join(own.get('headings', {}).get('h1', [])) or 'None'}
  H2 Tags ({len(own.get('headings', {}).get('h2', []))}): {', '.join(own.get('headings', {}).get('h2', [])) or 'None'}
  H3 Tags ({len(own.get('headings', {}).get('h3', []))}): {', '.join(own.get('headings', {}).get('h3', [])[:10]) or 'None'}
  Keyword in H1: {'YES' if own.get('headings', {}).get('keyword_in_h1') else 'NO'}
  Keyword in H2: {'YES' if own.get('headings', {}).get('keyword_in_h2') else 'NO'}

CONTENT:
  Word Count: {own.get('content', {}).get('word_count', 0)}
  Keyword Occurrences: {own.get('content', {}).get('keyword_count', 0)}

IMAGES:
  Total: {own.get('images', {}).get('total', 0)}
  Missing Alt Text: {own.get('images', {}).get('missing_alt', 0)}
  Examples without alt: {', '.join(own.get('images', {}).get('missing_alt_list', [])[:5]) or 'None'}

LINKS:
  Internal: {own.get('links', {}).get('internal', 0)}
  External: {own.get('links', {}).get('external', 0)}

TECHNICAL:
  Schema Markup: {', '.join(own.get('technical', {}).get('schema_types', [])) or 'None'}
  Open Graph: {'YES' if own.get('social', {}).get('has_og_tags') else 'NO'}
  Twitter Cards: {'YES' if own.get('social', {}).get('has_twitter_cards') else 'NO'}
  Viewport: {'YES' if own.get('technical', {}).get('has_viewport') else 'NO'}
  Load Time: {own.get('load_time', 0)}s
  Page Size: {own.get('page_size_kb', 0)}KB

CONTENT TEXT (first 2500 chars):
{own.get('content', {}).get('text', '')[:2500]}
"""

        # Format competitor data
        comp_sections = []
        for i, comp in enumerate(competitors, 1):
            comp_section = f"""
--- COMPETITOR #{i}: {comp.get('domain', 'Unknown')} ---
URL: {comp.get('url', 'N/A')}
Title: "{comp.get('title', {}).get('text', '')}" ({comp.get('title', {}).get('length', 0)} chars, keyword: {'YES' if comp.get('title', {}).get('has_keyword') else 'NO'})
Meta Description: "{comp.get('meta_description', {}).get('text', '')}" ({comp.get('meta_description', {}).get('length', 0)} chars)

HEADINGS:
  H1: {', '.join(comp.get('headings', {}).get('h1', [])) or 'None'}
  H2: {', '.join(comp.get('headings', {}).get('h2', [])) or 'None'}
  H3: {', '.join(comp.get('headings', {}).get('h3', [])[:8]) or 'None'}
  Keyword in H1: {'YES' if comp.get('headings', {}).get('keyword_in_h1') else 'NO'}
  Keyword in H2: {'YES' if comp.get('headings', {}).get('keyword_in_h2') else 'NO'}

METRICS:
  Word Count: {comp.get('content', {}).get('word_count', 0)}
  Keyword Count: {comp.get('content', {}).get('keyword_count', 0)}
  Images: {comp.get('images', {}).get('total', 0)} (missing alt: {comp.get('images', {}).get('missing_alt', 0)})
  Internal Links: {comp.get('links', {}).get('internal', 0)}
  Schema: {', '.join(comp.get('technical', {}).get('schema_types', [])) or 'None'}

CONTENT TEXT (first 1500 chars):
{comp.get('content', {}).get('text', '')[:1500]}
"""
            comp_sections.append(comp_section)

        return dedent(f"""\
You are an expert SEO consultant analyzing crawled website data.
Your task is to create a WINNING STRATEGY to help my website outrank competitors for the keyword: "{keyword}"

I have crawled my page and {len(competitors)} competitor pages. Here is ALL the data:

================================================================================
MY WEBSITE
================================================================================
{own_section}

================================================================================
COMPETITORS
================================================================================
{''.join(comp_sections)}

================================================================================
ANALYSIS REQUIRED
================================================================================

Based on this COMPLETE data, provide your analysis in HTML format (no markdown):

<h3>1. Data Summary Table</h3>
Create a comparison table showing key metrics for all pages side by side.

<h3>2. Keyword Optimization Analysis</h3>
- How does my keyword usage compare to competitors?
- Where am I using the keyword well? Where am I missing opportunities?
- What keyword variations are competitors using that I'm not?

<h3>3. Content Analysis</h3>
- Compare content depth, structure, and quality
- What topics/sections do competitors cover that I don't?
- What unique content do I have that competitors lack?

<h3>4. Technical SEO Comparison</h3>
- Compare schema markup, meta tags, heading structure
- What technical advantages/disadvantages do I have?

<h3>5. Image Optimization</h3>
- Analyze image usage and alt text optimization across all sites
- Specific recommendations for my images

<h3>6. WINNING STRATEGY</h3>
Based on ALL the data above, provide a detailed, prioritized action plan:
- Phase 1: Quick wins (implement this week)
- Phase 2: Content improvements (this month)
- Phase 3: Technical optimizations
- Phase 4: Long-term competitive advantages

<h3>7. Specific Recommendations</h3>
- Exact title tag to use
- Exact meta description to use
- Specific H2 headings to add
- Content sections to create

Format as clean HTML with tables, lists, and emphasis where appropriate.
Be specific and actionable - reference actual data from the crawl.
""")


    def _build_audit_only_prompt(self, keyword: str, own_page) -> str:
        """Build SEO audit prompt for single page analysis (no competitors)."""
        issues_list = "\n".join([
            f"- [{i.severity.upper()}] {i.category}: {i.message}"
            for i in own_page.issues
        ]) or "No critical issues detected."

        return dedent(f"""\
            You are an expert SEO consultant. Analyse the following page data and provide
            specific, actionable recommendations to improve its SEO for the keyword: "{keyword}"

            === PAGE ANALYSIS ===
            URL: {own_page.url}
            Title: {own_page.title}
            Title Length: {len(own_page.title)} characters
            Meta Description: {own_page.meta_description}
            Meta Description Length: {len(own_page.meta_description)} characters
            H1 Tags: {', '.join(own_page.h1_tags[:5]) or 'None'}
            H2 Tags: {', '.join(own_page.h2_tags[:10]) or 'None'}
            H3 Tags: {', '.join(own_page.h3_tags[:10]) or 'None'}
            Word Count: {own_page.word_count}
            Images: {own_page.images_total} total, {own_page.images_missing_alt} missing alt text
            Internal Links: {own_page.internal_links}
            External Links: {own_page.external_links}
            Schema Types: {', '.join(own_page.schema_types) or 'None'}
            Has Open Graph: {own_page.has_og_tags}
            Has Twitter Cards: {own_page.has_twitter_cards}
            Has Viewport Meta: {own_page.has_viewport}
            Load Time: {own_page.load_time}s
            Page Size: {own_page.page_size_kb}KB

            === DETECTED ISSUES ===
            {issues_list}

            === CONTENT PREVIEW (first 2000 chars) ===
            {own_page.content_text[:2000]}

            Please provide your response in the following HTML structure (no markdown):
            1. <h3>Title & Meta Description Improvements</h3> — suggest better title and
               meta description that would improve CTR, include the keyword naturally.
            2. <h3>Content Recommendations</h3> — how can the content be improved for
               better keyword targeting and user engagement?
            3. <h3>Content Structure Recommendations</h3> — suggest specific H2/H3
               subheadings that should be added for better SEO.
            4. <h3>Technical SEO Fixes</h3> — based on the issues detected, what should
               be fixed first and how?
            5. <h3>Schema Markup Suggestions</h3> — what structured data should be added?
            6. <h3>Quick Wins</h3> — 3-5 easy changes that can be made today for immediate impact.

            Keep suggestions specific based on the actual content. Format as clean HTML
            with <ul>, <li>, <p>, <strong> tags.
        """)


# ---------------------------------------------------------------------------
# HTML Report Generator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Generate a comprehensive Markdown report."""

    @staticmethod
    def generate(
        keyword: str, own_domain: str, own_page: Optional[PageAnalysis],
        competitors: list[PageAnalysis], serp_results: list[SERPResult],
        own_serp: Optional[SERPResult], ai_suggestions: str,
    ) -> str:
        """Build the full Markdown report."""

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        own_position = own_serp.position if own_serp else "Not in top results"

        # Count issues by severity
        critical = sum(1 for i in (own_page.issues if own_page else []) if i.severity == "critical")
        warnings = sum(1 for i in (own_page.issues if own_page else []) if i.severity == "warning")
        infos = sum(1 for i in (own_page.issues if own_page else []) if i.severity == "info")

        # Build SERP table
        serp_rows = ""
        for sr in serp_results:
            is_own = "★" if own_domain.replace("www.", "") in sr.domain else ""
            serp_rows += f"| {sr.position} | {is_own} {sr.domain} | [{sr.title or sr.url[:50]}]({sr.url}) |\n"

        # Build issues list
        issues_list = ""
        if own_page:
            for issue in own_page.issues:
                severity_icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(issue.severity, "⚪")
                issues_list += f"| {severity_icon} **{issue.severity.upper()}** | {issue.category} | {issue.message} | {issue.suggestion} |\n"

        # Build comparison table
        comparison_rows = ""
        all_pages = []
        if own_page:
            all_pages.append(own_page)
        all_pages.extend(competitors)

        for p in all_pages:
            own_marker = " ★" if p.is_own_page else ""
            kw_title = "✓" if getattr(p, 'keyword_in_title', False) else "✗"
            kw_h1 = "✓" if getattr(p, 'keyword_in_h1', False) else "✗"
            kw_count = getattr(p, 'keyword_count', 0)
            comparison_rows += f"| #{p.serp_position or '-'} | {p.domain}{own_marker} | {len(p.title)} | {len(p.meta_description)} | {len(p.h1_tags)} | {len(p.h2_tags)} | {p.word_count} | {kw_title} | {kw_h1} | {kw_count}x | {', '.join(p.schema_types[:3]) or 'None'} |\n"

        # Convert HTML AI suggestions to Markdown
        ai_md = ReportGenerator._html_to_markdown(ai_suggestions)

        report = f"""# SEO Audit Report

**Keyword:** "{keyword}"
**Domain:** {own_domain}
**Generated:** {now}
**Server:** {os.uname().nodename}

---

## Summary

| Metric | Value |
|--------|-------|
| SERP Position | {own_position} |
| Critical Issues | {critical} |
| Warnings | {warnings} |
| Info | {infos} |
| Word Count | {own_page.word_count if own_page else 'N/A'} |
| Competitors Analysed | {len(competitors)} |

---

## Side-by-Side Comparison

| # | Domain | Title | Meta | H1s | H2s | Words | KW Title | KW H1 | KW Count | Schema |
|---|--------|-------|------|-----|-----|-------|----------|-------|----------|--------|
{comparison_rows}

---

## SEO Issues Found

| Severity | Category | Issue | Suggestion |
|----------|----------|-------|------------|
{issues_list if issues_list else "| ✅ | - | No issues found | - |"}

---

## AI Content Suggestions

{ai_md}

---

{"## Google SERP Results" + chr(10) + chr(10) + "| # | Domain | Page |" + chr(10) + "|---|--------|------|" + chr(10) + serp_rows + chr(10) + "---" + chr(10) if serp_rows else ""}

*Generated by SEO Competitive Auditor v1.1*
"""
        return report

    @staticmethod
    def _html_to_markdown(html: str) -> str:
        """Convert HTML to Markdown."""
        import re

        if not html:
            return "*No AI suggestions available.*"

        md = html

        # Convert headers
        md = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1', md, flags=re.DOTALL)
        md = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1', md, flags=re.DOTALL)
        md = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1', md, flags=re.DOTALL)

        # Convert lists
        md = re.sub(r'<ul[^>]*>', '', md)
        md = re.sub(r'</ul>', '', md)
        md = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', md, flags=re.DOTALL)

        # Convert formatting
        md = re.sub(r'<strong>(.*?)</strong>', r'**\1**', md, flags=re.DOTALL)
        md = re.sub(r'<b>(.*?)</b>', r'**\1**', md, flags=re.DOTALL)
        md = re.sub(r'<em>(.*?)</em>', r'*\1*', md, flags=re.DOTALL)
        md = re.sub(r'<i>(.*?)</i>', r'*\1*', md, flags=re.DOTALL)

        # Convert paragraphs
        md = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n', md, flags=re.DOTALL)

        # Convert links
        md = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', md, flags=re.DOTALL)

        # Remove remaining HTML tags
        md = re.sub(r'<[^>]+>', '', md)

        # Clean up whitespace
        md = re.sub(r'\n\s*\n\s*\n', '\n\n', md)
        md = md.strip()

        return md


# ---------------------------------------------------------------------------
# Email sender
# ---------------------------------------------------------------------------

def export_crawl_data(keyword: str, own_page: 'PageAnalysis', competitors: list, report_dir: str) -> tuple[str, str]:
    """
    Export all crawled data to CSV and JSON files for analysis.
    Returns (csv_path, json_path).
    """
    timestamp = datetime.now().strftime('%Y%m%d-%H%M')
    safe_keyword = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")

    # Prepare data for all pages
    all_pages = []
    if own_page:
        all_pages.append(('own', own_page))
    for i, cp in enumerate(competitors, 1):
        all_pages.append((f'competitor_{i}', cp))

    # CSV Export
    csv_path = os.path.join(report_dir, f"seo-data-{safe_keyword}-{timestamp}.csv")
    csv_headers = [
        'type', 'url', 'domain', 'status_code', 'load_time',
        'title', 'title_length', 'meta_description', 'meta_desc_length',
        'canonical', 'h1_tags', 'h1_count', 'h2_tags', 'h2_count',
        'h3_tags', 'h3_count', 'word_count',
        'keyword', 'keyword_in_title', 'keyword_in_h1', 'keyword_in_h2',
        'keyword_in_meta', 'keyword_count',
        'images_total', 'images_missing_alt', 'images_missing_alt_list',
        'internal_links', 'external_links',
        'has_og_tags', 'has_twitter_cards', 'has_schema', 'schema_types',
        'has_viewport', 'has_hreflang', 'page_size_kb',
        'content_preview'
    ]

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=csv_headers)
        writer.writeheader()

        for page_type, page in all_pages:
            row = {
                'type': page_type,
                'url': page.url,
                'domain': page.domain,
                'status_code': page.status_code,
                'load_time': page.load_time,
                'title': page.title,
                'title_length': len(page.title),
                'meta_description': page.meta_description,
                'meta_desc_length': len(page.meta_description),
                'canonical': page.canonical,
                'h1_tags': ' | '.join(page.h1_tags),
                'h1_count': len(page.h1_tags),
                'h2_tags': ' | '.join(page.h2_tags),
                'h2_count': len(page.h2_tags),
                'h3_tags': ' | '.join(page.h3_tags),
                'h3_count': len(page.h3_tags),
                'word_count': page.word_count,
                'keyword': keyword,
                'keyword_in_title': getattr(page, 'keyword_in_title', False),
                'keyword_in_h1': getattr(page, 'keyword_in_h1', False),
                'keyword_in_h2': getattr(page, 'keyword_in_h2', False),
                'keyword_in_meta': getattr(page, 'keyword_in_meta', False),
                'keyword_count': getattr(page, 'keyword_count', 0),
                'images_total': page.images_total,
                'images_missing_alt': page.images_missing_alt,
                'images_missing_alt_list': ' | '.join(page.images_missing_alt_list[:10]),
                'internal_links': page.internal_links,
                'external_links': page.external_links,
                'has_og_tags': page.has_og_tags,
                'has_twitter_cards': page.has_twitter_cards,
                'has_schema': page.has_schema_markup,
                'schema_types': ', '.join(page.schema_types),
                'has_viewport': page.has_viewport,
                'has_hreflang': page.has_hreflang,
                'page_size_kb': page.page_size_kb,
                'content_preview': page.content_text[:500].replace('\n', ' ')
            }
            writer.writerow(row)

    # JSON Export (more detailed)
    json_path = os.path.join(report_dir, f"seo-data-{safe_keyword}-{timestamp}.json")
    json_data = {
        'keyword': keyword,
        'timestamp': timestamp,
        'own_page': None,
        'competitors': []
    }

    for page_type, page in all_pages:
        page_data = {
            'type': page_type,
            'url': page.url,
            'domain': page.domain,
            'status_code': page.status_code,
            'load_time': page.load_time,
            'page_size_kb': page.page_size_kb,
            'title': {
                'text': page.title,
                'length': len(page.title),
                'has_keyword': getattr(page, 'keyword_in_title', False)
            },
            'meta_description': {
                'text': page.meta_description,
                'length': len(page.meta_description),
                'has_keyword': getattr(page, 'keyword_in_meta', False)
            },
            'canonical': page.canonical,
            'headings': {
                'h1': page.h1_tags,
                'h2': page.h2_tags,
                'h3': page.h3_tags,
                'keyword_in_h1': getattr(page, 'keyword_in_h1', False),
                'keyword_in_h2': getattr(page, 'keyword_in_h2', False)
            },
            'content': {
                'word_count': page.word_count,
                'keyword_count': getattr(page, 'keyword_count', 0),
                'text': page.content_text[:3000]  # First 3000 chars
            },
            'images': {
                'total': page.images_total,
                'missing_alt': page.images_missing_alt,
                'missing_alt_list': page.images_missing_alt_list[:20]
            },
            'links': {
                'internal': page.internal_links,
                'external': page.external_links
            },
            'social': {
                'has_og_tags': page.has_og_tags,
                'has_twitter_cards': page.has_twitter_cards
            },
            'technical': {
                'has_schema': page.has_schema_markup,
                'schema_types': page.schema_types,
                'has_viewport': page.has_viewport,
                'has_hreflang': page.has_hreflang,
                'robots_meta': page.has_robots_meta
            }
        }

        if page_type == 'own':
            json_data['own_page'] = page_data
        else:
            json_data['competitors'].append(page_data)

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    log.info(f"Data exported: {csv_path}")
    log.info(f"Data exported: {json_path}")

    return csv_path, json_path


def send_report_email(config: dict, keyword: str, domain: str, report_content: str, report_path: str):
    """Send the report via email."""
    emails = config.get("alert_emails", [])
    if not emails:
        log.warning("No alert_emails configured — skipping email")
        return

    server_name = os.uname().nodename
    subject = f"[{server_name}] SEO Audit: \"{keyword}\" — {domain}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.get("smtp_user") or f"seo-auditor@{server_name}"
    msg["To"] = ", ".join(emails)

    # Send markdown as plain text (it's readable)
    text_part = MIMEText(report_content, "plain", "utf-8")
    msg.attach(text_part)

    try:
        smtp_host = config["smtp_host"]
        smtp_port = config["smtp_port"]
        encryption = config.get("smtp_encryption", "none").lower()

        # Choose connection type based on encryption mode
        if encryption == "ssl":
            # Port 465: Implicit SSL (encrypted from start)
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        elif encryption == "starttls":
            # Port 587: Explicit TLS (upgrade after connect)
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
        else:
            # Port 25: No encryption
            server = smtplib.SMTP(smtp_host, smtp_port)

        if config.get("smtp_user") and config.get("smtp_pass"):
            server.login(config["smtp_user"], config["smtp_pass"])

        server.sendmail(msg["From"], emails, msg.as_string())
        server.quit()
        log.info(f"Report emailed to: {', '.join(emails)}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")
        log.info(f"Report saved locally at: {report_path}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_audit(keyword: str, own_domain: str, own_url: str = "", config: dict = None):
    """Main audit pipeline."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    audit_only = cfg.get("audit_only", False)

    # Check for SERPAPI or manual competitors
    serpapi_key = cfg.get("serpapi_key", "")
    manual_competitors = cfg.get("competitors", [])
    use_manual_competitors = not serpapi_key and manual_competitors

    # Auto-enable audit-only mode if neither SERPAPI nor manual competitors configured
    if not audit_only and not serpapi_key and not manual_competitors:
        log.warning("No SERPAPI_KEY or COMPETITORS configured — switching to audit-only mode")
        log.info("TIP: Add COMPETITORS=domain1.com,domain2.com to .env for competitor analysis")
        audit_only = True

    log.info("=" * 60)
    if audit_only:
        log.info(f"SEO Website Audit (Single Page)")
    elif use_manual_competitors:
        log.info(f"SEO Competitive Audit (Manual Competitors)")
    else:
        log.info(f"SEO Competitive Audit (SERP-based)")
    log.info(f"Keyword: {keyword}")
    log.info(f"Domain:  {own_domain}")
    if use_manual_competitors:
        log.info(f"Competitors: {', '.join(manual_competitors[:5])}")
    log.info("=" * 60)

    serp_results = []
    own_serp = None
    competitor_pages = []

    # --- Step 1: Search Google or use manual competitors ---
    if not audit_only and not use_manual_competitors:
        # Use SerpAPI for SERP-based analysis
        serp = SERPChecker(cfg)
        serp_results, own_serp = serp.search(keyword, own_domain)

        if not serp_results:
            log.error("No SERP results found — cannot continue")
            log.info("TIP: Use --audit-only or add COMPETITORS to .env")
            return

    # --- Step 2: Determine our URL ---
    if own_url:
        our_url = own_url
    elif own_serp:
        our_url = own_serp.url
    else:
        # Not ranking or audit-only mode — use homepage or a likely URL
        our_url = f"https://www.{own_domain}/"
        log.info(f"Using URL: {our_url}")

    # --- Step 3: Crawl and analyse pages ---
    analyser = PageAnalyser(cfg)

    # Crawl our page (with full HTML for image analysis)
    log.info("\nAnalysing YOUR page:")
    own_page = analyser.analyse(our_url, is_own=True)
    own_page.serp_position = own_serp.position if own_serp else 0
    own_page.is_own_page = True

    # Re-fetch for image analysis
    try:
        resp = requests.get(our_url, headers={"User-Agent": USER_AGENT}, timeout=cfg["request_timeout"])
        if resp.status_code == 200:
            analyse_images_from_html(resp.text, our_url, own_page)
            # Re-run image check
            if own_page.images_missing_alt > 0:
                own_page.issues.append(SEOIssue(
                    "warning", "images",
                    f"{own_page.images_missing_alt} of {own_page.images_total} images missing alt text",
                    "Add descriptive alt text to all images for accessibility and image SEO."
                ))
    except Exception:
        pass

    # Analyze keyword presence in own page
    keyword_lower = keyword.lower()
    content_lower = own_page.content_text.lower()
    title_lower = own_page.title.lower()

    own_page.keyword_count = content_lower.count(keyword_lower)
    own_page.keyword_in_title = keyword_lower in title_lower
    own_page.keyword_in_h1 = any(keyword_lower in h.lower() for h in own_page.h1_tags)
    own_page.keyword_in_h2 = any(keyword_lower in h.lower() for h in own_page.h2_tags)
    own_page.keyword_in_meta = keyword_lower in own_page.meta_description.lower()

    log.info(f"    Keyword '{keyword}' in your page: {own_page.keyword_count}x in content, "
             f"title={'yes' if own_page.keyword_in_title else 'no'}, "
             f"H1={'yes' if own_page.keyword_in_h1 else 'no'}, "
             f"meta={'yes' if own_page.keyword_in_meta else 'no'}")

    # Crawl competitor pages
    if not audit_only:
        if use_manual_competitors:
            # Use manually configured competitors
            log.info(f"\nAnalysing {min(len(manual_competitors), cfg['top_competitors'])} configured competitors:")
            for i, comp in enumerate(manual_competitors[:cfg["top_competitors"]], 1):
                time.sleep(cfg["request_delay"])

                # Build competitor URL (support both domains and full URLs)
                if comp.startswith("http"):
                    comp_base_url = comp
                else:
                    comp_base_url = f"https://{comp}/"

                # Find the best page for this keyword on competitor site
                log.info(f"  Competitor #{i}: {comp}")
                comp_url = analyser.find_best_page_for_keyword(comp_base_url, keyword)

                cp = analyser.analyse(comp_url, is_own=False)
                cp.serp_position = i  # Use order as position

                # Analyze keyword presence in competitor content
                keyword_lower = keyword.lower()
                content_lower = cp.content_text.lower()
                title_lower = cp.title.lower()

                # Count keyword occurrences
                keyword_count = content_lower.count(keyword_lower)
                keyword_in_title = keyword_lower in title_lower
                keyword_in_h1 = any(keyword_lower in h.lower() for h in cp.h1_tags)
                keyword_in_h2 = any(keyword_lower in h.lower() for h in cp.h2_tags)

                log.info(f"    Keyword '{keyword}' found {keyword_count}x in content, "
                         f"title={'yes' if keyword_in_title else 'no'}, "
                         f"H1={'yes' if keyword_in_h1 else 'no'}, "
                         f"H2={'yes' if keyword_in_h2 else 'no'}")

                # Store keyword analysis in page object
                cp.keyword_count = keyword_count
                cp.keyword_in_title = keyword_in_title
                cp.keyword_in_h1 = keyword_in_h1
                cp.keyword_in_h2 = keyword_in_h2

                # Image analysis
                try:
                    resp = requests.get(comp_url, headers={"User-Agent": USER_AGENT}, timeout=cfg["request_timeout"])
                    if resp.status_code == 200:
                        analyse_images_from_html(resp.text, comp_url, cp)
                except Exception:
                    pass

                competitor_pages.append(cp)

        elif serp_results:
            # Use SERP results
            log.info(f"\nAnalysing top {cfg['top_competitors']} competitors from SERP:")
            for sr in serp_results:
                if own_domain.replace("www.", "") in sr.domain:
                    continue
                if len(competitor_pages) >= cfg["top_competitors"]:
                    break

                time.sleep(cfg["request_delay"])
                cp = analyser.analyse(sr.url, is_own=False)
                cp.serp_position = sr.position

                # Image analysis for competitors too
                try:
                    resp = requests.get(sr.url, headers={"User-Agent": USER_AGENT}, timeout=cfg["request_timeout"])
                    if resp.status_code == 200:
                        analyse_images_from_html(resp.text, sr.url, cp)
                except Exception:
                    pass

                competitor_pages.append(cp)
    else:
        log.info("\nSkipping competitor analysis (--audit-only mode)")

    # --- Step 4: Export crawl data ---
    log.info("\nExporting crawl data...")
    csv_path, json_path = export_crawl_data(keyword, own_page, competitor_pages, cfg["report_dir"])

    # Load JSON data for AI analysis
    with open(json_path, 'r', encoding='utf-8') as f:
        crawl_data = json.load(f)

    # --- Step 5: AI Content Suggestions ---
    advisor = AIAdvisor(cfg)
    ai_suggestions = advisor.compare_and_suggest(keyword, own_page, competitor_pages, crawl_data)

    # --- Step 6: Generate Report ---
    log.info("\nGenerating report...")
    report_html = ReportGenerator.generate(
        keyword=keyword,
        own_domain=own_domain,
        own_page=own_page,
        competitors=competitor_pages,
        serp_results=serp_results,
        own_serp=own_serp,
        ai_suggestions=ai_suggestions,
    )

    # Save report
    os.makedirs(cfg["report_dir"], exist_ok=True)
    safe_keyword = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    report_filename = f"seo-audit-{safe_keyword}-{own_domain}-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
    report_path = os.path.join(cfg["report_dir"], report_filename)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_html)
    log.info(f"Report saved: {report_path}")

    # --- Step 7: Email Report ---
    send_report_email(cfg, keyword, own_domain, report_html, report_path)

    # Summary
    log.info("\n" + "=" * 60)
    log.info("AUDIT COMPLETE")
    log.info(f"Your position: #{own_page.serp_position or 'Not ranking'}")
    log.info(f"Issues found: {len(own_page.issues)} "
             f"({sum(1 for i in own_page.issues if i.severity == 'critical')} critical)")
    log.info(f"Competitors analysed: {len(competitor_pages)}")
    log.info(f"Report: {report_path}")
    log.info("=" * 60)

    return report_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SEO Competitive Auditor — analyse your page vs Google competitors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples:
              python3 seo-auditor.py --keyword "oak flooring uk" --domain flooringsuppliescentre.co.uk
              python3 seo-auditor.py --keyword "engineered wood flooring" --domain oakparquetflooring.co.uk --provider openai
              python3 seo-auditor.py --keyword "laminate flooring" --domain tradescentre.co.uk --provider claude --top 3
              python3 seo-auditor.py --keyword "vinyl flooring" --domain tradescentre.co.uk --no-ai

            AI Provider (--provider):
              auto     — Uses whichever API key is set (default)
              claude   — Anthropic Claude (best quality, ~$3/MTok input)
              openai   — OpenAI GPT (cheapest with gpt-4.1-mini at ~$0.30/MTok input)

            Configuration:
              Copy .env.example to .env and configure your settings.
              All environment variables are documented in .env.example.

              Key variables:
              ANTHROPIC_API_KEY   — Required for --provider claude
              OPENAI_API_KEY      — Required for --provider openai
              ALERT_EMAILS        — Comma-separated list of email recipients
              SMTP_HOST/PORT/USER/PASS  — Email server settings
              GOOGLE_COUNTRY      — Google domain (co.uk, com, de, etc.)
              REPORT_DIR          — Where to save HTML reports
        """)
    )
    parser.add_argument("-k", "--keyword", required=True, help="Target keyword to audit")
    parser.add_argument("-d", "--domain", required=True, help="Your domain (e.g. flooringsuppliescentre.co.uk)")
    parser.add_argument("-u", "--url", default="", help="Specific URL to audit (default: auto-detect from SERP or homepage)")
    parser.add_argument("-t", "--top", type=int, default=5, help="Number of competitors to analyse (default: 5)")
    parser.add_argument("-p", "--provider", choices=["auto", "claude", "openai"], default="auto",
                        help="AI provider for content suggestions (default: auto)")
    parser.add_argument("-m", "--model", default="", help="Override AI model (e.g. gpt-5-mini, gpt-5-nano, claude-sonnet-4-20250514)")
    parser.add_argument("-e", "--emails", nargs="+", default=None, help="Override alert emails")
    parser.add_argument("-o", "--output-dir", default=None, help="Report output directory (default: from .env or /tmp/seo-reports)")
    parser.add_argument("--country", default="co.uk", help="Google country domain (default: co.uk)")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI content suggestions entirely")
    parser.add_argument("--audit-only", action="store_true",
                        help="Only audit the specified URL, skip SERP/competitor analysis")

    args = parser.parse_args()

    config = {
        "top_competitors": args.top,
        "google_country": args.country,
        "ai_provider": args.provider,
        "audit_only": args.audit_only,
    }

    if args.output_dir:
        config["report_dir"] = args.output_dir

    if args.emails:
        config["alert_emails"] = args.emails

    if args.model:
        if args.provider == "openai" or (args.provider == "auto" and os.getenv("OPENAI_API_KEY")):
            config["openai_model"] = args.model
        else:
            config["claude_model"] = args.model

    if args.no_ai:
        config["ai_provider"] = "none"
        config["anthropic_api_key"] = ""
        config["openai_api_key"] = ""

    run_audit(
        keyword=args.keyword,
        own_domain=args.domain,
        own_url=args.url,
        config=config,
    )


if __name__ == "__main__":
    main()
