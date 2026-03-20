# SEO Agent - Command Reference

## Quick Start

```bash
cd "/var/www/html/PROJECTS/SEO agent"
source venv/bin/activate
```

---

## 1. SEO Auditor (`seo-auditor.py`)

Analyzes your page against competitors for a specific keyword.

```bash
# Basic audit (uses competitors from .env)
python seo-auditor.py --keyword "спално бельо памучен сатен" --domain hrimarcomfort.com

# Audit with specific number of competitors
python seo-auditor.py --keyword "памучен сатен" --domain hrimarcomfort.com --top 5

# Audit single page only (no competitor analysis)
python seo-auditor.py --keyword "памучен сатен" --domain hrimarcomfort.com --audit-only

# Use specific AI provider
python seo-auditor.py --keyword "памучен сатен" --domain hrimarcomfort.com --provider claude
python seo-auditor.py --keyword "памучен сатен" --domain hrimarcomfort.com --provider openai

# Custom report directory
python seo-auditor.py --keyword "памучен сатен" --domain hrimarcomfort.com --report-dir ./my-reports
```

**Output:** Text report in `reports/` folder with:
- Keyword analysis table
- Page comparison table
- SEO issues found
- AI recommendations
- Article suggestions / Content gaps

---

## 2. Batch Auditor (`seo-audit-batch.py`)

Run audits for multiple keywords from a file.

```bash
# Run batch audit
python seo-audit-batch.py --input keywords.txt --domain hrimarcomfort.com

# With custom delay between audits
python seo-audit-batch.py --input keywords.txt --domain hrimarcomfort.com --delay 60
```

**Input file format (keywords.txt):**
```
спално бельо памучен сатен
качествено спално бельо
ранфорс спално бельо
```

---

## 3. Knowledge Base (`seo-knowledge-base.py`)

Persistent storage for sitemap data, keywords, and SERP rankings.

```bash
# Update sitemap data (crawls your site)
python seo-knowledge-base.py --update-sitemap

# Update with custom URL
python seo-knowledge-base.py --update-sitemap --url https://example.com

# Limit pages to crawl
python seo-knowledge-base.py --update-sitemap --max 100

# Track a keyword
python seo-knowledge-base.py --track-keyword "качествено спално бельо"

# Get AI context for a keyword (shows what AI will see)
python seo-knowledge-base.py --get-context "памучен сатен"

# Show database statistics
python seo-knowledge-base.py --stats
```

**Database:** `seo-knowledge.db` (SQLite)

---

## 4. Sitemap Scraper (`sitemap-scraper.py`)

Scrapes sitemap URLs with titles and meta descriptions (legacy, use knowledge base instead).

```bash
# Scrape sitemap
python sitemap-scraper.py --url https://www.hrimarcomfort.com

# Limit URLs
python sitemap-scraper.py --url https://www.hrimarcomfort.com --max 100

# Custom output file
python sitemap-scraper.py --url https://www.hrimarcomfort.com --output my-urls.json
```

**Output:** `site-urls-{domain}.json`

---

## 5. Article Generator (`wp-article-generator.py`)

Generates SEO-optimized articles using AI and optionally publishes to WordPress.

```bash
# Generate blog article (saved locally)
python wp-article-generator.py --keyword "памучен сатен 83 нишки" --type blog

# Generate guide article
python wp-article-generator.py --keyword "как да изберем спално бельо" --type guide

# Generate product description
python wp-article-generator.py --keyword "памучен сатен" --type product

# Generate FAQ article
python wp-article-generator.py --keyword "спално бельо въпроси" --type faq

# Generate comparison article
python wp-article-generator.py --keyword "памучен сатен vs ранфорс" --type comparison

# Generate case-study (comprehensive deep-dive)
python wp-article-generator.py --keyword "83 нишки срещу 40 нишки" --type case-study

# Use Elementor-compatible format
python wp-article-generator.py --keyword "памучен сатен" --type case-study --format elementor

# Use WPBakery format
python wp-article-generator.py --keyword "памучен сатен" --type guide --format wpbakery

# With additional context
python wp-article-generator.py --keyword "памучен сатен" --type guide \
  --context "Focus on 83 threads/cm² quality. Brand: Hrimar Comfort"

# Publish to WordPress as draft
python wp-article-generator.py --keyword "памучен сатен" --type blog --publish

# Publish with specific status
python wp-article-generator.py --keyword "памучен сатен" --type blog --publish --status draft
python wp-article-generator.py --keyword "памучен сатен" --type blog --publish --status publish
python wp-article-generator.py --keyword "памучен сатен" --type blog --publish --status pending

# Publish to specific category
python wp-article-generator.py --keyword "памучен сатен" --type blog --publish --category 5

# Test WordPress connection
python wp-article-generator.py --test-wp

# Change language
python wp-article-generator.py --keyword "cotton sateen" --type blog --language en
```

**Article Types:**
| Type | Description | Word Count |
|------|-------------|------------|
| `blog` | Informative blog post | 1000-1500 |
| `guide` | Comprehensive guide with TOC | 1500-2500 |
| `product` | Product category description | 800-1200 |
| `faq` | FAQ article (8-12 questions) | Variable |
| `comparison` | Comparison with tables | 1200-1800 |
| `case-study` | Deep-dive comprehensive article | 2500-4000 |

**Output Formats:**
| Format | Description |
|--------|-------------|
| `html` | Clean responsive HTML (default) |
| `elementor` | Elementor widget-compatible structure |
| `wpbakery` | WPBakery/Visual Composer shortcodes |

**Output:**
- `articles/article-{keyword}-{date}.json` - Article data
- `articles/article-{keyword}-{date}.html` - Preview

---

## Configuration (.env)

Copy `.env.example` to `.env` and configure:

```bash
# Required - AI API Keys (at least one)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Optional - SERP tracking
SERPAPI_KEY=your-key

# Manual competitors (when no SerpAPI)
COMPETITORS=competitor1.com,competitor2.com,competitor3.com

# WordPress (for article publishing)
WP_URL=https://your-site.com
WP_USER=username
WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Google settings
GOOGLE_COUNTRY=bg
GOOGLE_LANG=bg
```

---

## Typical Workflow

### 1. Initial Setup
```bash
# Update knowledge base with your site data
python seo-knowledge-base.py --update-sitemap --max 200
```

### 2. Run SEO Audit
```bash
# Audit for target keyword
python seo-auditor.py --keyword "спално бельо памучен сатен" --domain hrimarcomfort.com
```

### 3. Track Keywords
```bash
# Track important keywords
python seo-knowledge-base.py --track-keyword "качествено спално бельо"
python seo-knowledge-base.py --track-keyword "памучен сатен"
```

### 4. Generate Content
```bash
# Generate article for content gap identified in audit
python wp-article-generator.py --keyword "памучен сатен 83 нишки качество" --type guide
```

### 5. Review and Publish
```bash
# Open HTML preview
xdg-open articles/article-*.html

# Publish to WordPress
python wp-article-generator.py --keyword "памучен сатен 83 нишки качество" --type guide --publish
```

---

## 6. Image Alt Text Optimizer (`wp-image-optimizer.py`)

Adds alt text and title attributes to WordPress images using AI.

```bash
# Test WordPress connection
python wp-image-optimizer.py --test

# List images missing alt text
python wp-image-optimizer.py --list-missing

# Preview changes (dry run)
python wp-image-optimizer.py --fix-all --dry-run

# Fix all images missing alt text
python wp-image-optimizer.py --fix-all

# Fix with keyword context (better alt text)
python wp-image-optimizer.py --fix-all --keywords "спално бельо, памучен сатен"

# Fix specific image by ID
python wp-image-optimizer.py --fix-image 123

# Limit number of images to process
python wp-image-optimizer.py --fix-all --limit 10
```

**Features:**
- Fetches all images from WordPress media library
- Uses AI to generate descriptive alt text from filename
- Falls back to filename parsing if no AI key
- Supports dry-run mode to preview changes
- Keyword context for better SEO-focused alt text

---

## Output Locations

| Type | Location |
|------|----------|
| SEO Reports | `reports/seo-audit--{domain}-{date}.txt` |
| Crawl Data | `reports/seo-data--{date}.csv` / `.json` |
| Articles | `articles/article-{keyword}-{date}.json` / `.html` |
| Knowledge Base | `seo-knowledge.db` |
| Site URLs | `site-urls-{domain}.json` |
