#!/usr/bin/env python3
"""
================================================================================
WordPress Article Generator v2.0
================================================================================
Generates SEO-optimized articles using AI and publishes them to WordPress.

Features:
- Multiple article types: blog, guide, product, faq, comparison, case-study
- Output formats: HTML, Elementor, WPBakery
- Internal link building from sitemap
- Knowledge base integration

Usage:
    python3 wp-article-generator.py --keyword "спално бельо памучен сатен" --type blog
    python3 wp-article-generator.py --keyword "как да изберем спално бельо" --type guide
    python3 wp-article-generator.py --keyword "памучен сатен" --type case-study --format elementor
    python3 wp-article-generator.py --keyword "ранфорс vs сатен" --type comparison --format wpbakery

Configuration:
    Set WordPress credentials in .env file:
    WP_URL=https://your-site.com
    WP_USER=your-username
    WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

Requirements:
    pip install requests python-dotenv anthropic openai
================================================================================
"""

import os
import sys
import re
import json
import argparse
import logging
import requests
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from base64 import b64encode
from glob import glob

# Try to import knowledge base
HAS_KNOWLEDGE_BASE = False
SEOKnowledgeBase = None
try:
    import importlib.util
    kb_path = Path(__file__).resolve().parent / "seo-knowledge-base.py"
    if kb_path.exists():
        spec = importlib.util.spec_from_file_location("seo_knowledge_base", kb_path)
        seo_kb_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seo_kb_module)
        SEOKnowledgeBase = seo_kb_module.SEOKnowledgeBase
        HAS_KNOWLEDGE_BASE = True
except Exception:
    pass

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

# Configuration
CONFIG = {
    # WordPress API
    "wp_url": os.getenv("WP_URL", ""),
    "wp_user": os.getenv("WP_USER", ""),
    "wp_app_password": os.getenv("WP_APP_PASSWORD", ""),

    # AI Provider
    "ai_provider": os.getenv("AI_PROVIDER", "auto"),
    "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "claude_model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
    "openai_model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),

    # Article defaults
    "default_status": os.getenv("WP_DEFAULT_STATUS", "draft"),  # draft, publish, pending
    "default_author": os.getenv("WP_DEFAULT_AUTHOR", ""),

    # Output
    "output_dir": os.getenv("ARTICLE_OUTPUT_DIR", str(script_dir / "articles")),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wp-generator")


# ==============================================================================
# Output Format Templates
# ==============================================================================

class OutputFormats:
    """Templates for different page builder formats."""

    @staticmethod
    def get_format_instructions(format_type: str) -> str:
        """Get formatting instructions for the specified output format."""

        formats = {
            "html": """
OUTPUT FORMAT: Clean Responsive HTML
Use semantic HTML5 with inline responsive styles:
- Use <section>, <article>, <aside> elements
- Include responsive CSS in style attributes or <style> block
- Use max-width, flexbox, and media queries for responsiveness
- Tables should have overflow-x: auto wrapper for mobile
- Images should have max-width: 100%; height: auto;
- Use relative units (rem, em, %) over pixels

Example structure:
<style>
.article-section { max-width: 1200px; margin: 0 auto; padding: 20px; }
.comparison-table { width: 100%; overflow-x: auto; }
.feature-grid { display: flex; flex-wrap: wrap; gap: 20px; }
.feature-card { flex: 1 1 300px; padding: 20px; background: #f8f9fa; border-radius: 8px; }
@media (max-width: 768px) { .feature-card { flex: 1 1 100%; } }
</style>
""",
            "elementor": """
OUTPUT FORMAT: Elementor-Compatible HTML
Structure content for Elementor page builder widgets:
- Use [elementor-template] shortcodes where appropriate
- Structure sections with data-elementor attributes
- Use CSS classes that map to Elementor styles:
  * elementor-section, elementor-column, elementor-widget
  * elementor-heading-title, elementor-text-editor
  * elementor-icon-box, elementor-image-box
  * elementor-accordion, elementor-tabs
  * elementor-counter, elementor-progress-bar

Include Elementor widget comments for easy copy-paste:
<!-- Elementor: Section with 2 columns -->
<!-- Elementor: Icon Box widget -->
<!-- Elementor: Comparison Table widget -->
<!-- Elementor: Accordion/FAQ widget -->
<!-- Elementor: Call to Action widget -->

Use these CSS classes for styling:
.elementor-section { padding: 40px 0; }
.elementor-column-33 { width: 33.33%; }
.elementor-column-50 { width: 50%; }
.elementor-cta { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
""",
            "wpbakery": """
OUTPUT FORMAT: WPBakery (Visual Composer) Compatible
Structure content using WPBakery shortcode format:
[vc_row][vc_column width="1/2"]content[/vc_column][/vc_row]

Common shortcodes to use:
- [vc_row] [vc_column] - Row and column structure
- [vc_column_text] - Text blocks
- [vc_single_image] - Images
- [vc_separator] - Dividers
- [vc_tta_accordion] [vc_tta_section] - Accordions/FAQs
- [vc_tta_tabs] - Tabs
- [vc_btn] - Buttons/CTAs
- [vc_icon] - Icons
- [vc_progress_bar] - Progress bars
- [vc_pie] - Pie charts
- [vc_custom_heading] - Styled headings

Example structure:
[vc_row][vc_column]
[vc_custom_heading text="Title" font_container="tag:h2|text_align:center"]
[vc_column_text]Content here[/vc_column_text]
[/vc_column][/vc_row]

[vc_row][vc_column width="1/3"]Card 1[/vc_column][vc_column width="1/3"]Card 2[/vc_column][vc_column width="1/3"]Card 3[/vc_column][/vc_row]
"""
        }

        return formats.get(format_type, formats["html"])


# ==============================================================================
# Site URL Database
# ==============================================================================

class SiteURLDatabase:
    """Load and search site URLs from scraped sitemap data."""

    def __init__(self, domain: str = None):
        self.pages = []
        self.posts = []
        self.categories = []
        self.products = []
        self.loaded = False

        # Try to find and load the site URLs JSON
        self._load_database(domain)

    def _load_database(self, domain: str = None):
        """Load site URLs from JSON file."""
        script_dir = Path(__file__).resolve().parent

        # Find matching JSON file
        if domain:
            # Look for domain-specific file
            domain_safe = domain.replace(".", "-").replace("www-", "")
            pattern = script_dir / f"site-urls-*{domain_safe}*.json"
        else:
            pattern = script_dir / "site-urls-*.json"

        json_files = list(glob(str(pattern)))

        if not json_files:
            # Try generic pattern
            json_files = list(glob(str(script_dir / "site-urls-*.json")))

        if json_files:
            # Use the most recent file
            json_file = max(json_files, key=os.path.getmtime)
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.pages = data.get("pages", [])
                    self.posts = data.get("posts", [])
                    self.categories = data.get("categories", [])
                    self.products = data.get("products", [])
                    self.loaded = True
                    log.info(f"Loaded {len(self.pages)} URLs from {json_file}")
            except Exception as e:
                log.warning(f"Failed to load site URLs: {e}")

    def find_relevant_urls(self, keyword: str, max_results: int = 10) -> list:
        """Find URLs relevant to the given keyword."""
        if not self.loaded:
            return []

        keyword_lower = keyword.lower()
        keywords = keyword_lower.split()

        scored_urls = []

        for page in self.pages:
            score = 0
            title = page.get("title", "").lower()
            h1 = page.get("h1", "").lower()
            url = page.get("url", "").lower()
            meta = page.get("meta_description", "").lower()

            # Score based on keyword matches
            for kw in keywords:
                if kw in title:
                    score += 3
                if kw in h1:
                    score += 2
                if kw in url:
                    score += 2
                if kw in meta:
                    score += 1

            # Bonus for posts (blog articles)
            if page.get("type") == "post":
                score += 1

            if score > 0:
                scored_urls.append((score, page))

        # Sort by score and return top results
        scored_urls.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "url": p["url"],
                "title": p.get("title", ""),
                "type": p.get("type", "page"),
            }
            for _, p in scored_urls[:max_results]
        ]

    def get_all_posts(self) -> list:
        """Get all blog posts."""
        return [
            {"url": p["url"], "title": p.get("title", "")}
            for p in self.posts
            if p.get("title")
        ]

    def get_all_categories(self) -> list:
        """Get all product categories."""
        return [
            {"url": p["url"], "title": p.get("title", "")}
            for p in self.categories
            if p.get("title")
        ]


# ==============================================================================
# AI Content Generator
# ==============================================================================

class AIContentGenerator:
    """Generate article content using Claude or OpenAI."""

    def __init__(self):
        self.provider = CONFIG["ai_provider"]
        self.anthropic_key = CONFIG["anthropic_api_key"]
        self.openai_key = CONFIG["openai_api_key"]

        # Auto-detect provider
        if self.provider == "auto":
            if self.anthropic_key:
                self.provider = "claude"
            elif self.openai_key:
                self.provider = "openai"
            else:
                self.provider = "none"

        self.enabled = self.provider != "none"
        if not self.enabled:
            log.error("No AI API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY")

        # Load site URL database for internal linking
        domain = CONFIG["wp_url"].replace("https://", "").replace("http://", "").split("/")[0]
        self.site_urls = SiteURLDatabase(domain)

        # Load knowledge base if available
        self.knowledge_base = None
        if HAS_KNOWLEDGE_BASE:
            try:
                self.knowledge_base = SEOKnowledgeBase()
                log.info("Knowledge base loaded")
            except Exception as e:
                log.debug(f"Could not load knowledge base: {e}")

    def generate_article(self, keyword: str, article_type: str = "blog",
                         language: str = "bg", extra_context: str = "",
                         output_format: str = "html") -> dict:
        """Generate a complete article structure."""

        if not self.enabled:
            return None

        prompt = self._build_prompt(keyword, article_type, language, extra_context, output_format)

        log.info(f"Generating {article_type} article for: '{keyword}'...")

        if self.provider == "claude":
            response = self._call_claude(prompt)
        else:
            response = self._call_openai(prompt)

        # Parse the JSON response
        if not response:
            log.error("Empty response from AI")
            return None

        try:
            # Extract JSON from response - find the outermost { }
            # Handle nested braces correctly
            depth = 0
            start_idx = -1
            end_idx = -1

            for i, char in enumerate(response):
                if char == '{':
                    if depth == 0:
                        start_idx = i
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0 and start_idx != -1:
                        end_idx = i + 1
                        break

            if start_idx != -1 and end_idx != -1:
                json_str = response[start_idx:end_idx]
                article_data = json.loads(json_str)
                return article_data
            else:
                log.warning("No JSON found in response, using raw content")
                return {
                    "title": f"Article about {keyword}",
                    "content": response,
                    "excerpt": "",
                    "meta_description": "",
                    "focus_keyword": keyword,
                    "tags": [keyword],
                }
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse AI response as JSON: {e}")
            log.debug(f"Response preview: {response[:500]}...")
            # Return raw content as fallback
            return {
                "title": f"Article about {keyword}",
                "content": response,
                "excerpt": "",
                "meta_description": "",
                "focus_keyword": keyword,
                "tags": [keyword],
            }

    def _build_prompt(self, keyword: str, article_type: str, language: str, extra_context: str, output_format: str = "html") -> str:
        """Build the article generation prompt."""

        # Get knowledge base context
        kb_context_section = ""
        if self.knowledge_base:
            try:
                kb_context = self.knowledge_base.format_context_for_prompt(keyword)
                if kb_context:
                    kb_context_section = f"""
SITE KNOWLEDGE BASE:
{kb_context}

IMPORTANT INSTRUCTIONS:
- DO NOT duplicate existing content - create something NEW and COMPLEMENTARY
- If similar articles exist, focus on a different angle or aspect
- Use the available pages for internal linking
- Consider the current SERP ranking when writing
"""
            except Exception as e:
                log.debug(f"Could not get KB context: {e}")

        # Find relevant internal links (fallback if KB not available)
        internal_links_section = ""
        if not kb_context_section and self.site_urls.loaded:
            relevant_urls = self.site_urls.find_relevant_urls(keyword, max_results=15)
            if relevant_urls:
                links_list = "\n".join([
                    f"  - {u['title']}: {u['url']}" for u in relevant_urls
                ])
                internal_links_section = f"""
AVAILABLE INTERNAL LINKS (use these real URLs in the content):
{links_list}

IMPORTANT: Include 3-5 of these REAL links naturally in the article content.
Use <a href="URL">anchor text</a> format with relevant anchor text.
"""

        type_instructions = {
            "blog": """
                Write an informative blog post that:
                - Is engaging and conversational
                - Provides valuable information to readers
                - Includes practical tips and advice
                - Has 1000-1500 words
            """,
            "guide": """
                Write a comprehensive guide that:
                - Is detailed and thorough
                - Includes step-by-step instructions where appropriate
                - Has clear sections with H2 and H3 headings
                - Is 1500-2500 words
                - Includes a table of contents
            """,
            "product": """
                Write a product category description that:
                - Highlights benefits and features
                - Is persuasive but informative
                - Includes buying advice
                - Has 800-1200 words
                - Focuses on helping customers make decisions
            """,
            "faq": """
                Write an FAQ article that:
                - Answers 8-12 common questions
                - Each answer is 50-150 words
                - Uses FAQ schema-friendly formatting
                - Is helpful and authoritative
            """,
            "comparison": """
                Write a comparison article that:
                - Compares different options objectively
                - Uses tables for easy comparison
                - Includes pros and cons
                - Has 1200-1800 words
                - Helps readers make informed decisions
            """,
            "case-study": """
                Write a comprehensive case-study/deep-dive article that:
                - Is extremely detailed and authoritative (2500-4000 words)
                - Includes technical data, statistics, and expert insights
                - Has clear sections with H2 and H3 hierarchy
                - Contains comparison tables with detailed specifications
                - Includes visual elements (charts, infographics descriptions)
                - Has FAQ section at the end (8-12 questions)
                - Provides actionable recommendations
                - Uses storytelling and real-world examples
                - Establishes thought leadership on the topic
                - Contains multiple CTAs throughout
                - HEAVILY focuses on internal linking (10-15 links)
                - Targets multiple related long-tail keywords
            """,
        }

        type_instruction = type_instructions.get(article_type, type_instructions["blog"])

        lang_map = {"bg": "Bulgarian", "en": "English", "de": "German"}
        language_name = lang_map.get(language, "Bulgarian")

        return dedent(f"""
            You are an expert SEO content writer. Generate a complete article in {language_name} language.

            TARGET KEYWORD: "{keyword}"
            ARTICLE TYPE: {article_type}

            {type_instruction}

            {f"ADDITIONAL CONTEXT: {extra_context}" if extra_context else ""}

            {kb_context_section}

            {internal_links_section}

            SEO REQUIREMENTS:
            - Include the exact keyword "{keyword}" in:
              * Title (naturally, near the beginning)
              * First paragraph
              * At least 2-3 H2 headings
              * Throughout the content (keyword density 1-2%)
              * Meta description
            - Use related keywords and synonyms naturally
            - Write for humans first, search engines second
            - Include 3-5 internal links using the REAL URLs provided above (if available)
            - Use descriptive anchor text for internal links

            {OutputFormats.get_format_instructions(output_format)}

            FORMATTING:
            - Use HTML formatting (h2, h3, p, ul, li, strong, em, table)
            - Do NOT use h1 (WordPress adds this from title)
            - Use short paragraphs (2-4 sentences)
            - Include bullet points and lists where appropriate
            - Add a compelling introduction and conclusion
            - Make ALL content responsive for mobile devices

            RESPOND WITH VALID JSON ONLY (no markdown, no code blocks):
            {{
                "title": "SEO-optimized title with keyword",
                "slug": "url-friendly-slug",
                "content": "<h2>Heading</h2><p>Content...</p>",
                "excerpt": "Compelling 150-200 char excerpt with keyword",
                "meta_description": "SEO meta description 150-160 chars with keyword",
                "focus_keyword": "{keyword}",
                "secondary_keywords": ["keyword1", "keyword2"],
                "tags": ["tag1", "tag2"],
                "suggested_categories": ["category1"],
                "internal_links": ["suggested page to link to"],
                "faq": [
                    {{"question": "Question?", "answer": "Answer"}}
                ]
            }}
        """)

    def _call_claude(self, prompt: str) -> str:
        """Call Anthropic Claude API."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.anthropic_key)
            model = CONFIG["claude_model"]
            log.info(f"Using Claude ({model})...")

            response = client.messages.create(
                model=model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            log.error(f"Claude API error: {e}")
            return ""

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API."""
        try:
            import openai
            client = openai.OpenAI(api_key=self.openai_key)
            model = CONFIG["openai_model"]
            log.info(f"Using OpenAI ({model})...")

            response = client.chat.completions.create(
                model=model,
                max_tokens=8000,
                messages=[
                    {"role": "system", "content": "You are an expert SEO content writer. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            log.error(f"OpenAI API error: {e}")
            return ""


# ==============================================================================
# WordPress Publisher
# ==============================================================================

class WordPressPublisher:
    """Publish content to WordPress via REST API."""

    def __init__(self):
        self.url = CONFIG["wp_url"].rstrip("/")
        self.user = CONFIG["wp_user"]
        self.password = CONFIG["wp_app_password"]

        if not all([self.url, self.user, self.password]):
            log.warning("WordPress credentials not fully configured in .env")
            self.enabled = False
        else:
            self.enabled = True
            self.api_url = f"{self.url}/wp-json/wp/v2"
            self.auth = b64encode(f"{self.user}:{self.password}".encode()).decode()
            self.headers = {
                "Authorization": f"Basic {self.auth}",
                "Content-Type": "application/json",
            }

    def test_connection(self) -> bool:
        """Test WordPress API connection."""
        if not self.enabled:
            return False

        try:
            resp = requests.get(f"{self.api_url}/users/me", headers=self.headers, timeout=10)
            if resp.status_code == 200:
                user_data = resp.json()
                log.info(f"Connected to WordPress as: {user_data.get('name', 'Unknown')}")
                return True
            else:
                log.error(f"WordPress auth failed: {resp.status_code} - {resp.text[:200]}")
                return False
        except Exception as e:
            log.error(f"WordPress connection error: {e}")
            return False

    def get_categories(self) -> list:
        """Get list of WordPress categories."""
        if not self.enabled:
            return []

        try:
            resp = requests.get(f"{self.api_url}/categories", headers=self.headers, params={"per_page": 100})
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.error(f"Failed to get categories: {e}")
        return []

    def get_tags(self) -> list:
        """Get list of WordPress tags."""
        if not self.enabled:
            return []

        try:
            resp = requests.get(f"{self.api_url}/tags", headers=self.headers, params={"per_page": 100})
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.error(f"Failed to get tags: {e}")
        return []

    def create_tag(self, name: str) -> int:
        """Create a new tag and return its ID."""
        if not self.enabled:
            return 0

        try:
            resp = requests.post(
                f"{self.api_url}/tags",
                headers=self.headers,
                json={"name": name}
            )
            if resp.status_code == 201:
                return resp.json().get("id", 0)
            elif resp.status_code == 400 and "term_exists" in resp.text:
                # Tag already exists, get its ID
                existing = requests.get(
                    f"{self.api_url}/tags",
                    headers=self.headers,
                    params={"search": name}
                )
                if existing.status_code == 200 and existing.json():
                    return existing.json()[0].get("id", 0)
        except Exception as e:
            log.error(f"Failed to create tag '{name}': {e}")
        return 0

    def publish_post(self, article: dict, status: str = "draft", category_ids: list = None) -> dict:
        """Publish article to WordPress."""
        if not self.enabled:
            return {"error": "WordPress not configured"}

        # Prepare tag IDs
        tag_ids = []
        for tag_name in article.get("tags", []):
            tag_id = self.create_tag(tag_name)
            if tag_id:
                tag_ids.append(tag_id)

        # Prepare post data
        post_data = {
            "title": article.get("title", "Untitled"),
            "slug": article.get("slug", ""),
            "content": article.get("content", ""),
            "excerpt": article.get("excerpt", ""),
            "status": status,
            "tags": tag_ids,
        }

        if category_ids:
            post_data["categories"] = category_ids

        # Add Yoast SEO meta if available (requires Yoast REST API)
        # This is handled separately through meta fields

        try:
            log.info(f"Publishing to WordPress as '{status}'...")
            resp = requests.post(
                f"{self.api_url}/posts",
                headers=self.headers,
                json=post_data
            )

            if resp.status_code == 201:
                result = resp.json()
                log.info(f"Published successfully! Post ID: {result.get('id')}")
                log.info(f"URL: {result.get('link')}")
                return {
                    "success": True,
                    "post_id": result.get("id"),
                    "url": result.get("link"),
                    "edit_url": f"{self.url}/wp-admin/post.php?post={result.get('id')}&action=edit",
                }
            else:
                log.error(f"Publish failed: {resp.status_code} - {resp.text[:300]}")
                return {"error": resp.text}

        except Exception as e:
            log.error(f"Publish error: {e}")
            return {"error": str(e)}


# ==============================================================================
# Article Saver
# ==============================================================================

def save_article_locally(article: dict, keyword: str, output_dir: str) -> str:
    """Save article to local file."""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d-%H%M')
    safe_keyword = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    filename = f"article-{safe_keyword}-{timestamp}.json"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(article, f, ensure_ascii=False, indent=2)

    log.info(f"Article saved: {filepath}")

    # Also save as HTML for preview
    html_filename = f"article-{safe_keyword}-{timestamp}.html"
    html_filepath = os.path.join(output_dir, html_filename)

    html_content = f"""<!DOCTYPE html>
<html lang="bg">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{article.get('meta_description', '')}">
    <title>{article.get('title', 'Article')}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
        h1 {{ color: #333; }}
        h2 {{ color: #444; margin-top: 30px; }}
        h3 {{ color: #555; }}
        .meta {{ background: #f5f5f5; padding: 15px; margin: 20px 0; border-radius: 8px; }}
        .meta strong {{ color: #666; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
        th {{ background: #f5f5f5; }}
        .faq {{ background: #fffde7; padding: 15px; margin: 20px 0; border-radius: 8px; }}
    </style>
</head>
<body>
    <h1>{article.get('title', 'Article')}</h1>

    <div class="meta">
        <p><strong>Focus Keyword:</strong> {article.get('focus_keyword', '')}</p>
        <p><strong>Meta Description:</strong> {article.get('meta_description', '')}</p>
        <p><strong>Tags:</strong> {', '.join(article.get('tags', []))}</p>
    </div>

    {article.get('content', '')}

    {'<div class="faq"><h2>FAQ</h2>' + ''.join([f"<h3>{faq.get('question', '')}</h3><p>{faq.get('answer', '')}</p>" for faq in article.get('faq', [])]) + '</div>' if article.get('faq') else ''}

</body>
</html>"""

    with open(html_filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)

    log.info(f"HTML preview: {html_filepath}")

    return filepath


# ==============================================================================
# Main
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate SEO-optimized articles and publish to WordPress",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""
            Examples:
              python3 wp-article-generator.py --keyword "спално бельо памучен сатен" --type blog
              python3 wp-article-generator.py --keyword "как да изберем спално бельо" --type guide
              python3 wp-article-generator.py --keyword "памучен сатен" --type case-study --format elementor
              python3 wp-article-generator.py --keyword "ранфорс vs сатен" --type comparison --format wpbakery

            Article Types:
              blog        - Informative blog post (1000-1500 words)
              guide       - Comprehensive guide (1500-2500 words)
              product     - Product category description (800-1200 words)
              faq         - FAQ article (8-12 questions)
              comparison  - Comparison article (1200-1800 words)
              case-study  - Deep-dive comprehensive article (2500-4000 words)

            Output Formats:
              html        - Clean responsive HTML (default)
              elementor   - Elementor widget-compatible structure
              wpbakery    - WPBakery/Visual Composer shortcodes
        """)
    )

    parser.add_argument("-k", "--keyword", required=True, help="Target keyword for the article")
    parser.add_argument("-t", "--type", default="blog",
                        choices=["blog", "guide", "product", "faq", "comparison", "case-study"],
                        help="Article type (default: blog)")
    parser.add_argument("-f", "--format", default="html",
                        choices=["html", "elementor", "wpbakery"],
                        help="Output format (default: html)")
    parser.add_argument("-l", "--language", default="bg", help="Language code (default: bg)")
    parser.add_argument("-c", "--category", type=int, nargs="+", help="WordPress category ID(s)")
    parser.add_argument("--context", default="", help="Additional context for AI")
    parser.add_argument("--publish", action="store_true", help="Publish to WordPress (default: save locally)")
    parser.add_argument("--status", default="draft", choices=["draft", "publish", "pending"],
                        help="WordPress post status (default: draft)")
    parser.add_argument("--test-wp", action="store_true", help="Test WordPress connection and exit")

    args = parser.parse_args()

    # Test WordPress connection
    wp = WordPressPublisher()

    if args.test_wp:
        if wp.test_connection():
            print("\nCategories:")
            for cat in wp.get_categories():
                print(f"  [{cat['id']}] {cat['name']}")
            print("\nTags:")
            for tag in wp.get_tags()[:20]:
                print(f"  [{tag['id']}] {tag['name']}")
        return

    # Generate article
    generator = AIContentGenerator()
    if not generator.enabled:
        log.error("AI not configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")
        return

    article = generator.generate_article(
        keyword=args.keyword,
        article_type=args.type,
        language=args.language,
        extra_context=args.context,
        output_format=args.format
    )

    if not article:
        log.error("Failed to generate article")
        return

    log.info(f"Generated article: {article.get('title', 'Untitled')}")
    log.info(f"Word count: ~{len(article.get('content', '').split())}")

    # Save locally
    save_article_locally(article, args.keyword, CONFIG["output_dir"])

    # Publish to WordPress if requested
    if args.publish:
        if not wp.enabled:
            log.warning("WordPress not configured. Article saved locally only.")
            return

        if not wp.test_connection():
            log.error("Cannot connect to WordPress")
            return

        result = wp.publish_post(
            article=article,
            status=args.status,
            category_ids=args.category
        )

        if result.get("success"):
            log.info(f"Edit post: {result.get('edit_url')}")
    else:
        log.info("Article saved locally. Use --publish to post to WordPress.")


if __name__ == "__main__":
    main()
