#!/usr/bin/env python3
"""
================================================================================
WordPress Image Alt Text Optimizer v1.0
================================================================================
Adds alt text and title attributes to images in WordPress media library.
Uses AI to generate descriptive alt text based on image content and filename.

Usage:
    # List images missing alt text
    python3 wp-image-optimizer.py --list-missing

    # Generate alt text for all images missing it
    python3 wp-image-optimizer.py --fix-all

    # Generate alt text for specific image ID
    python3 wp-image-optimizer.py --fix-image 123

    # Preview changes without applying
    python3 wp-image-optimizer.py --fix-all --dry-run

    # Use keywords for context
    python3 wp-image-optimizer.py --fix-all --keywords "спално бельо, памучен сатен"

Configuration:
    Set WordPress credentials in .env file:
    WP_URL=https://your-site.com
    WP_USER=your-username
    WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
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
from base64 import b64encode
from urllib.parse import urlparse, unquote

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
log = logging.getLogger("wp-image-optimizer")

# Configuration
CONFIG = {
    "wp_url": os.getenv("WP_URL", ""),
    "wp_user": os.getenv("WP_USER", ""),
    "wp_app_password": os.getenv("WP_APP_PASSWORD", ""),
    "ai_provider": os.getenv("AI_PROVIDER", "auto"),
    "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "claude_model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
    "openai_model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
}


class WordPressMediaAPI:
    """Interact with WordPress Media Library via REST API."""

    def __init__(self):
        self.url = CONFIG["wp_url"].rstrip("/")
        self.user = CONFIG["wp_user"]
        self.password = CONFIG["wp_app_password"]

        if not all([self.url, self.user, self.password]):
            log.error("WordPress credentials not configured in .env")
            log.error("Required: WP_URL, WP_USER, WP_APP_PASSWORD")
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
            resp = requests.get(
                f"{self.api_url}/users/me",
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 200:
                user_data = resp.json()
                log.info(f"Connected to WordPress as: {user_data.get('name', 'Unknown')}")
                return True
            else:
                log.error(f"WordPress auth failed: {resp.status_code}")
                return False
        except Exception as e:
            log.error(f"WordPress connection error: {e}")
            return False

    def get_all_media(self, media_type: str = "image") -> list:
        """Get all media items from WordPress."""
        if not self.enabled:
            return []

        all_media = []
        page = 1
        per_page = 100

        while True:
            try:
                resp = requests.get(
                    f"{self.api_url}/media",
                    headers=self.headers,
                    params={
                        "per_page": per_page,
                        "page": page,
                        "media_type": media_type,
                    },
                    timeout=30
                )

                if resp.status_code != 200:
                    break

                items = resp.json()
                if not items:
                    break

                all_media.extend(items)
                log.info(f"  Fetched page {page}: {len(items)} images")

                # Check if there are more pages
                total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
                if page >= total_pages:
                    break

                page += 1

            except Exception as e:
                log.error(f"Failed to fetch media page {page}: {e}")
                break

        return all_media

    def get_media_missing_alt(self) -> list:
        """Get images that are missing alt text."""
        all_media = self.get_all_media()

        missing = []
        for item in all_media:
            alt_text = item.get("alt_text", "").strip()
            if not alt_text:
                missing.append({
                    "id": item.get("id"),
                    "title": item.get("title", {}).get("rendered", ""),
                    "filename": Path(urlparse(item.get("source_url", "")).path).name,
                    "url": item.get("source_url", ""),
                    "date": item.get("date", ""),
                    "mime_type": item.get("mime_type", ""),
                })

        return missing

    def update_media(self, media_id: int, alt_text: str, title: str = None) -> bool:
        """Update media item with alt text and title."""
        if not self.enabled:
            return False

        data = {"alt_text": alt_text}
        if title:
            data["title"] = title

        try:
            resp = requests.post(
                f"{self.api_url}/media/{media_id}",
                headers=self.headers,
                json=data,
                timeout=15
            )

            if resp.status_code == 200:
                return True
            else:
                log.error(f"Failed to update media {media_id}: {resp.status_code}")
                return False

        except Exception as e:
            log.error(f"Failed to update media {media_id}: {e}")
            return False


class AltTextGenerator:
    """Generate alt text using AI."""

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

    def generate_alt_text(self, filename: str, url: str = None,
                          keywords: list = None, context: str = None) -> dict:
        """Generate alt text and title from filename and context."""

        if not self.enabled:
            # Fallback: generate from filename
            return self._generate_from_filename(filename)

        prompt = self._build_prompt(filename, url, keywords, context)

        if self.provider == "claude":
            response = self._call_claude(prompt)
        else:
            response = self._call_openai(prompt)

        # Parse response
        try:
            # Try to extract JSON
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "alt_text": data.get("alt_text", ""),
                    "title": data.get("title", ""),
                }
        except:
            pass

        # Fallback
        return self._generate_from_filename(filename)

    def _generate_from_filename(self, filename: str) -> dict:
        """Generate alt text from filename (no AI)."""
        # Remove extension
        name = Path(filename).stem

        # Clean up filename
        # Replace common separators with spaces
        name = re.sub(r'[-_]+', ' ', name)

        # Remove numbers at start/end
        name = re.sub(r'^\d+\s*', '', name)
        name = re.sub(r'\s*\d+$', '', name)

        # Remove common image suffixes
        name = re.sub(r'\s*(scaled|rotated|cropped|edited|\d+x\d+)$', '', name, flags=re.I)

        # Capitalize words
        name = name.strip().title()

        if not name:
            name = "Image"

        return {
            "alt_text": name,
            "title": name,
        }

    def _build_prompt(self, filename: str, url: str = None,
                      keywords: list = None, context: str = None) -> str:
        """Build prompt for AI."""

        keywords_str = ", ".join(keywords) if keywords else ""

        return f"""Generate SEO-optimized alt text and title for an image.

FILENAME: {filename}
{f"IMAGE URL: {url}" if url else ""}
{f"RELEVANT KEYWORDS: {keywords_str}" if keywords_str else ""}
{f"CONTEXT: {context}" if context else ""}

REQUIREMENTS:
1. Alt text should be descriptive (what the image shows)
2. Alt text should be 5-15 words, natural language
3. Include relevant keywords naturally if provided
4. Title should be shorter (2-5 words)
5. Language: Match the filename language (Bulgarian if Cyrillic, English otherwise)
6. Do NOT start with "Image of" or "Picture of"
7. Be specific and descriptive

RESPOND WITH JSON ONLY:
{{"alt_text": "descriptive alt text here", "title": "Short Title"}}"""

    def _call_claude(self, prompt: str) -> str:
        """Call Claude API."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.anthropic_key)

            response = client.messages.create(
                model=CONFIG["claude_model"],
                max_tokens=200,
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

            response = client.chat.completions.create(
                model=CONFIG["openai_model"],
                max_tokens=200,
                messages=[
                    {"role": "system", "content": "Generate alt text for images. Respond with JSON only."},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            log.error(f"OpenAI API error: {e}")
            return ""


def list_missing_alt(wp_api: WordPressMediaAPI):
    """List all images missing alt text."""
    log.info("Fetching media library...")
    missing = wp_api.get_media_missing_alt()

    if not missing:
        print("\nNo images missing alt text!")
        return

    print(f"\n{'='*70}")
    print(f"IMAGES MISSING ALT TEXT: {len(missing)}")
    print(f"{'='*70}\n")

    print(f"{'ID':<8} {'FILENAME':<40} {'DATE':<12}")
    print(f"{'-'*8} {'-'*40} {'-'*12}")

    for img in missing:
        filename = img['filename'][:38] + '..' if len(img['filename']) > 40 else img['filename']
        date = img['date'][:10] if img['date'] else 'N/A'
        print(f"{img['id']:<8} {filename:<40} {date:<12}")

    print(f"\nTotal: {len(missing)} images need alt text")


def fix_image(wp_api: WordPressMediaAPI, generator: AltTextGenerator,
              image_id: int, keywords: list = None, dry_run: bool = False):
    """Fix alt text for a single image."""

    # Get image details
    try:
        resp = requests.get(
            f"{wp_api.api_url}/media/{image_id}",
            headers=wp_api.headers,
            timeout=10
        )
        if resp.status_code != 200:
            log.error(f"Image {image_id} not found")
            return False

        image = resp.json()
    except Exception as e:
        log.error(f"Failed to fetch image {image_id}: {e}")
        return False

    filename = Path(urlparse(image.get("source_url", "")).path).name
    url = image.get("source_url", "")

    log.info(f"Processing: {filename}")

    # Generate alt text
    result = generator.generate_alt_text(
        filename=filename,
        url=url,
        keywords=keywords,
    )

    alt_text = result.get("alt_text", "")
    title = result.get("title", "")

    log.info(f"  Alt text: {alt_text}")
    log.info(f"  Title: {title}")

    if dry_run:
        log.info("  [DRY RUN] Would update image")
        return True

    # Update image
    if wp_api.update_media(image_id, alt_text, title):
        log.info(f"  Updated successfully!")
        return True
    else:
        log.error(f"  Failed to update")
        return False


def is_uuid_filename(filename: str) -> bool:
    """Check if filename is a UUID (not descriptive)."""
    import re
    name = Path(filename).stem
    # UUID pattern
    uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
    # Also check for ChatGPT generated names
    chatgpt_pattern = r'^ChatGPT-Image-'
    return bool(re.match(uuid_pattern, name, re.I)) or bool(re.match(chatgpt_pattern, name))


def should_skip_filename(filename: str, skip_patterns: list = None) -> bool:
    """Check if filename should be skipped based on patterns."""
    if not skip_patterns:
        return False
    name = filename.lower()
    for pattern in skip_patterns:
        if pattern.lower() in name:
            return True
    return False


def fix_all_images(wp_api: WordPressMediaAPI, generator: AltTextGenerator,
                   keywords: list = None, dry_run: bool = False, limit: int = None,
                   skip_uuid: bool = False, delay: float = 1.0, skip_patterns: list = None):
    """Fix alt text for all images missing it."""
    import time

    log.info("Fetching images missing alt text...")
    missing = wp_api.get_media_missing_alt()

    if not missing:
        print("\nNo images missing alt text!")
        return

    # Filter out UUID filenames if requested
    if skip_uuid:
        original_count = len(missing)
        missing = [img for img in missing if not is_uuid_filename(img['filename'])]
        log.info(f"Skipped {original_count - len(missing)} UUID/generic filenames")

    # Filter out pattern-matched filenames
    if skip_patterns:
        original_count = len(missing)
        missing = [img for img in missing if not should_skip_filename(img['filename'], skip_patterns)]
        log.info(f"Skipped {original_count - len(missing)} files matching patterns: {skip_patterns}")

    if limit:
        missing = missing[:limit]

    log.info(f"Processing {len(missing)} images...")

    success = 0
    failed = 0
    skipped = 0

    for i, img in enumerate(missing, 1):
        filename = img['filename']
        log.info(f"\n[{i}/{len(missing)}] {filename}")

        # Check if UUID filename (use simple fallback)
        if is_uuid_filename(filename):
            if keywords:
                # Use first keyword for generic images
                alt_text = f"{keywords[0]} - изображение"
                title = keywords[0].title()
            else:
                log.info("  [SKIPPED] UUID filename without keywords")
                skipped += 1
                continue
        else:
            # Generate alt text with AI
            result = generator.generate_alt_text(
                filename=filename,
                url=img['url'],
                keywords=keywords,
            )
            alt_text = result.get("alt_text", "")
            title = result.get("title", "")

        log.info(f"  Alt: {alt_text}")
        log.info(f"  Title: {title}")

        if dry_run:
            log.info("  [DRY RUN] Skipping update")
            success += 1
        else:
            # Update
            if wp_api.update_media(img['id'], alt_text, title):
                log.info("  Updated!")
                success += 1
            else:
                log.error("  Failed!")
                failed += 1

        # Delay to avoid rate limiting
        if i < len(missing) and delay > 0:
            time.sleep(delay)

    print(f"\n{'='*50}")
    print(f"COMPLETED")
    print(f"{'='*50}")
    print(f"  Success: {success}")
    print(f"  Failed: {failed}")
    print(f"  Skipped: {skipped}")
    if dry_run:
        print(f"  (Dry run - no changes made)")


def main():
    parser = argparse.ArgumentParser(
        description="Add alt text to WordPress images using AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 wp-image-optimizer.py --list-missing
  python3 wp-image-optimizer.py --fix-all --dry-run
  python3 wp-image-optimizer.py --fix-all --keywords "спално бельо, памучен сатен"
  python3 wp-image-optimizer.py --fix-image 123
        """
    )

    parser.add_argument("--list-missing", action="store_true",
                        help="List images missing alt text")
    parser.add_argument("--fix-all", action="store_true",
                        help="Fix all images missing alt text")
    parser.add_argument("--fix-image", type=int, metavar="ID",
                        help="Fix specific image by ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without applying")
    parser.add_argument("--keywords", type=str,
                        help="Comma-separated keywords for context")
    parser.add_argument("--limit", type=int,
                        help="Limit number of images to process")
    parser.add_argument("--skip-uuid", action="store_true",
                        help="Skip images with UUID/generic filenames")
    parser.add_argument("--skip-patterns", type=str,
                        help="Comma-separated patterns to skip (e.g., 'furniture,wd-')")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Delay between API calls in seconds (default: 1.5)")
    parser.add_argument("--test", action="store_true",
                        help="Test WordPress connection")

    args = parser.parse_args()

    # Initialize
    wp_api = WordPressMediaAPI()

    if not wp_api.enabled:
        sys.exit(1)

    # Test connection
    if args.test:
        wp_api.test_connection()
        return

    if not wp_api.test_connection():
        sys.exit(1)

    generator = AltTextGenerator()

    # Parse keywords
    keywords = None
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",")]

    # Execute command
    if args.list_missing:
        list_missing_alt(wp_api)

    # Parse skip patterns
    skip_patterns = None
    if args.skip_patterns:
        skip_patterns = [p.strip() for p in args.skip_patterns.split(",")]

    if args.fix_all:
        fix_all_images(
            wp_api,
            generator,
            keywords=keywords,
            dry_run=args.dry_run,
            limit=args.limit,
            skip_uuid=args.skip_uuid,
            delay=args.delay,
            skip_patterns=skip_patterns
        )

    elif args.fix_image:
        fix_image(
            wp_api,
            generator,
            args.fix_image,
            keywords=keywords,
            dry_run=args.dry_run
        )

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
