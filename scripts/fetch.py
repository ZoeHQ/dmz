#!/usr/bin/env python3
"""
fetch.py — URL → Content materializer

Reads URL files from fetch/queue/, fetches content via Jina Reader,
writes markdown to fetch/output/ with timestamp-prefixed filenames.

Falls back to Playwright for JS-heavy sites (Claude/ChatGPT shares).
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote


QUEUE_DIR = Path("fetch/queue")
OUTPUT_DIR = Path("fetch/output")
JINA_READER_PREFIX = "https://r.jina.ai/"

# URLs that require JavaScript rendering
JS_REQUIRED_PATTERNS = [
    r"claude\.ai/share/",
    r"chatgpt\.com/share/",
    r"chat\.openai\.com/share/",
]

# Content patterns that indicate we got a login page instead of real content
LOGIN_PAGE_INDICATORS = [
    "Continue with Google",
    "Continue with email",
    "Log in",
    "Sign up",
    "Create an account",
]

# Cloudflare challenge indicators
CLOUDFLARE_INDICATORS = [
    "Just a moment...",
    "Verify you are human",
    "checking your browser",
    "Enable JavaScript and cookies",
    "Ray ID:",
    "cloudflare",
]


def parse_input_file(content: str) -> list[dict]:
    """
    Parse input file content into list of {url, note} dicts.

    Handles:
    - Single URL
    - URL with note (blank line separated)
    - Multiple URLs (markdown list)
    - JSON format
    """
    content = content.strip()
    if not content:
        return []

    # Try JSON first
    if content.startswith("{") or content.startswith("["):
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return [{"url": data.get("url", ""), "note": data.get("note", "")}]
            elif isinstance(data, list):
                return [{"url": item.get("url", ""), "note": item.get("note", "")} for item in data]
        except json.JSONDecodeError:
            pass  # Fall through to other formats

    # Check for markdown list format (lines starting with "- http")
    lines = content.split("\n")
    list_pattern = re.compile(r"^[-*]\s+(https?://\S+)(?:\s+[—–-]\s+(.*))?$")

    list_items = []
    for line in lines:
        match = list_pattern.match(line.strip())
        if match:
            list_items.append({"url": match.group(1), "note": match.group(2) or ""})

    if list_items:
        return list_items

    # Single URL or URL with note
    # First non-empty line should be URL
    url_pattern = re.compile(r"^https?://\S+$")

    parts = content.split("\n\n", 1)
    first_part = parts[0].strip()

    # Check if first line is a URL
    first_line = first_part.split("\n")[0].strip()
    if url_pattern.match(first_line):
        note = parts[1].strip() if len(parts) > 1 else ""
        return [{"url": first_line, "note": note}]

    # Try to find any URL in the content
    url_match = re.search(r"https?://\S+", content)
    if url_match:
        return [{"url": url_match.group(0), "note": ""}]

    return []


def needs_js_rendering(url: str) -> bool:
    """Check if URL requires JavaScript rendering."""
    for pattern in JS_REQUIRED_PATTERNS:
        if re.search(pattern, url):
            return True
    return False


def is_login_page(content: str) -> bool:
    """Check if content appears to be a login page instead of real content."""
    for indicator in LOGIN_PAGE_INDICATORS:
        if indicator in content:
            return True
    return False


def is_cloudflare_challenge(content: str) -> bool:
    """Check if content is a Cloudflare challenge page."""
    content_lower = content.lower()
    matches = sum(1 for indicator in CLOUDFLARE_INDICATORS if indicator.lower() in content_lower)
    # Need at least 2 indicators to confirm it's Cloudflare
    return matches >= 2


def fetch_via_jina(url: str) -> dict:
    """
    Fetch URL content via Jina Reader.

    Returns dict with success, title, content, error.
    """
    jina_url = JINA_READER_PREFIX + url

    try:
        req = urllib.request.Request(
            jina_url,
            headers={
                "User-Agent": "ZoeHQ-Fetch/1.0",
                "Accept": "text/plain",
            }
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode("utf-8")

            # Check if we got a login page instead of real content
            if is_login_page(content):
                return {
                    "success": False,
                    "title": "",
                    "content": "",
                    "error": "Got login page instead of content (JS rendering required)"
                }

            # Jina Reader returns markdown with title as first # heading
            title = ""
            title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            if title_match:
                title = title_match.group(1).strip()
            else:
                # Fallback: use domain as title
                parsed = urlparse(url)
                title = parsed.netloc

            return {
                "success": True,
                "title": title,
                "content": content,
                "error": None
            }
    except urllib.error.HTTPError as e:
        return {
            "success": False,
            "title": "",
            "content": "",
            "error": f"HTTP {e.code}: {e.reason}"
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "title": "",
            "content": "",
            "error": f"URL Error: {e.reason}"
        }
    except Exception as e:
        return {
            "success": False,
            "title": "",
            "content": "",
            "error": str(e)
        }


def fetch_via_playwright(url: str) -> dict:
    """
    Fetch URL content via Playwright (headless browser).

    Used for JS-heavy sites like Claude/ChatGPT shares.
    Uses playwright-stealth to help bypass bot detection.
    Returns dict with success, title, content, error.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False,
            "title": "",
            "content": "",
            "error": "Playwright not installed. Run: pip install playwright && playwright install chromium"
        }

    # Try to import stealth plugin
    try:
        from playwright_stealth import stealth_sync
        has_stealth = True
    except ImportError:
        has_stealth = False
        print("    → playwright-stealth not available, using standard browser")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = context.new_page()

            # Apply stealth mode if available
            if has_stealth:
                stealth_sync(page)
                print("    → Stealth mode applied")

            # Different wait strategies based on URL
            # Claude/ChatGPT have persistent connections, so networkidle never fires
            if "claude.ai/share" in url:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                content, title = extract_claude_share(page)
            elif "chatgpt.com/share" in url or "chat.openai.com/share" in url:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                content, title = extract_chatgpt_share(page)
            else:
                # Generic: try networkidle for regular pages
                page.goto(url, wait_until="networkidle", timeout=60000)
                title = page.title()
                content = page.content()
                # Try to get main content
                main = page.query_selector("main, article, .content, #content")
                if main:
                    content = main.inner_text()
                else:
                    content = page.query_selector("body").inner_text()

            browser.close()

            if not content or len(content.strip()) < 100:
                return {
                    "success": False,
                    "title": "",
                    "content": "",
                    "error": "Failed to extract meaningful content"
                }

            # Check for Cloudflare challenge page
            if is_cloudflare_challenge(content) or title == "Just a moment...":
                return {
                    "success": False,
                    "title": "",
                    "content": "",
                    "error": "Blocked by Cloudflare challenge (bot detection)"
                }

            return {
                "success": True,
                "title": title,
                "content": content,
                "error": None
            }

    except Exception as e:
        return {
            "success": False,
            "title": "",
            "content": "",
            "error": f"Playwright error: {str(e)}"
        }


def extract_claude_share(page) -> tuple[str, str]:
    """Extract conversation content from Claude share page."""
    import time

    # Give the page time to render JS content
    time.sleep(3)

    # Try multiple selector strategies for Claude's conversation
    selectors_to_try = [
        '[data-testid*="message"]',
        '[class*="ConversationItem"]',
        '[class*="message"]',
        '[class*="Message"]',
        '[class*="turn"]',
        '[class*="Turn"]',
        'div[class*="prose"]',
    ]

    # Wait for any conversation content to appear
    for selector in selectors_to_try:
        try:
            page.wait_for_selector(selector, timeout=10000)
            break
        except:
            continue

    title = page.title()
    if " - Claude" in title:
        title = title.replace(" - Claude", "").strip()
    if "Claude" == title:
        title = "Claude Conversation"

    # Extract conversation turns
    messages = []

    # Try each selector
    turns = []
    for selector in selectors_to_try:
        turns = page.query_selector_all(selector)
        if turns:
            break

    if not turns:
        # Fallback: get all text content from main area
        main = page.query_selector("main")
        if main:
            text = main.inner_text()
            # Clean up the text
            if text and len(text) > 100:
                return f"# {title}\n\n{text}", title

        # Last resort: get body text
        body = page.query_selector("body")
        if body:
            text = body.inner_text()
            return f"# {title}\n\n{text}", title

    for turn in turns:
        text = turn.inner_text().strip()
        if text and len(text) > 10:  # Skip very short fragments
            messages.append(text)

    if not messages:
        # Fallback to main content
        main = page.query_selector("main")
        if main:
            return f"# {title}\n\n{main.inner_text()}", title

    content = "\n\n---\n\n".join(messages)

    # Format as markdown
    markdown = f"# {title}\n\n{content}"

    return markdown, title


def extract_chatgpt_share(page) -> tuple[str, str]:
    """Extract conversation content from ChatGPT share page."""
    # Wait for conversation to load
    page.wait_for_selector('[class*="agent-turn"], [class*="user-turn"], [data-message-author-role]', timeout=30000)

    title = page.title()
    if " | ChatGPT" in title:
        title = title.replace(" | ChatGPT", "").strip()
    if "ChatGPT - " in title:
        title = title.replace("ChatGPT - ", "").strip()

    # Extract conversation turns
    messages = []

    # Try different selectors for ChatGPT's conversation structure
    turns = page.query_selector_all('[data-message-author-role], [class*="agent-turn"], [class*="user-turn"]')

    if not turns:
        # Fallback: get main content
        main = page.query_selector("main")
        if main:
            return main.inner_text(), title

    for turn in turns:
        role = turn.get_attribute("data-message-author-role") or ""
        text = turn.inner_text().strip()

        if text:
            if role == "user":
                messages.append(f"**Human:**\n{text}")
            elif role == "assistant":
                messages.append(f"**Assistant:**\n{text}")
            else:
                messages.append(text)

    content = "\n\n---\n\n".join(messages)

    # Format as markdown
    markdown = f"# {title}\n\n{content}"

    return markdown, title


def fetch_url(url: str) -> dict:
    """
    Fetch URL content, using appropriate method.

    1. If URL needs JS rendering, use Playwright directly
    2. Otherwise, try Jina Reader first
    3. Fall back to Playwright if Jina returns login page
    """
    # Check if URL requires JS rendering
    if needs_js_rendering(url):
        print(f"    → JS rendering required, using Playwright")
        return fetch_via_playwright(url)

    # Try Jina Reader first
    result = fetch_via_jina(url)

    if result["success"]:
        return result

    # If Jina failed with login page indicator, try Playwright
    if "login page" in result.get("error", "").lower() or "JS rendering" in result.get("error", ""):
        print(f"    → Jina got login page, falling back to Playwright")
        return fetch_via_playwright(url)

    return result


def slugify(text: str, max_length: int = 50) -> str:
    """Convert text to URL-friendly slug."""
    # Remove non-alphanumeric chars, replace spaces with hyphens
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug[:max_length]


def write_output(url: str, title: str, content: str, note: str, timestamp: datetime) -> Path:
    """
    Write fetched content to output directory.

    Returns path to created file.
    """
    # Create timestamp-prefixed filename
    ts_str = timestamp.strftime("%Y-%m-%dT%H%M%S")
    title_slug = slugify(title) if title else slugify(urlparse(url).netloc)
    filename = f"{ts_str}-{title_slug}.md"

    # Build frontmatter
    frontmatter = f"""---
url: {url}
title: {title}
fetched_at: {timestamp.isoformat()}
"""
    if note:
        # Escape note for YAML
        escaped_note = note.replace('"', '\\"')
        frontmatter += f'source_note: "{escaped_note}"\n'
    frontmatter += "---\n\n"

    # Combine frontmatter and content
    full_content = frontmatter + content

    # Write file
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename
    output_path.write_text(full_content, encoding="utf-8")

    return output_path


def process_queue():
    """Process all files in the queue directory."""
    if not QUEUE_DIR.exists():
        print(f"Queue directory {QUEUE_DIR} does not exist")
        return

    # Get all files except .gitkeep
    queue_files = [f for f in QUEUE_DIR.iterdir() if f.is_file() and f.name != ".gitkeep"]

    if not queue_files:
        print("No files in queue")
        return

    print(f"Found {len(queue_files)} file(s) in queue")

    results = {"success": 0, "failed": 0, "files_processed": []}

    for queue_file in queue_files:
        print(f"\nProcessing: {queue_file.name}")

        try:
            content = queue_file.read_text(encoding="utf-8")
            urls = parse_input_file(content)

            if not urls:
                print(f"  No URLs found in {queue_file.name}")
                results["failed"] += 1
                continue

            print(f"  Found {len(urls)} URL(s)")

            for i, item in enumerate(urls):
                url = item["url"]
                note = item["note"]

                if not url:
                    print(f"  Skipping empty URL")
                    continue

                print(f"  Fetching: {url}")

                # Fetch content (Jina first, Playwright fallback)
                fetch_result = fetch_url(url)

                if fetch_result["success"]:
                    # Use slightly offset timestamps for multiple URLs
                    timestamp = datetime.now(timezone.utc)
                    if i > 0:
                        # Add seconds offset for ordering
                        from datetime import timedelta
                        timestamp = timestamp + timedelta(seconds=i)

                    output_path = write_output(
                        url=url,
                        title=fetch_result["title"],
                        content=fetch_result["content"],
                        note=note,
                        timestamp=timestamp
                    )
                    print(f"  ✓ Written: {output_path.name}")
                    results["success"] += 1
                else:
                    print(f"  ✗ Failed: {fetch_result['error']}")
                    results["failed"] += 1

            # Delete processed queue file
            queue_file.unlink()
            results["files_processed"].append(queue_file.name)
            print(f"  Deleted: {queue_file.name}")

        except Exception as e:
            print(f"  Error processing {queue_file.name}: {e}")
            results["failed"] += 1

    print(f"\n--- Summary ---")
    print(f"URLs fetched: {results['success']}")
    print(f"URLs failed: {results['failed']}")
    print(f"Queue files processed: {len(results['files_processed'])}")

    # Exit with error if any failures
    if results["failed"] > 0:
        sys.exit(1)


def fetch_single_url(url: str, note: str = ""):
    """Fetch a single URL (for manual/workflow dispatch)."""
    print(f"Fetching: {url}")

    fetch_result = fetch_url(url)

    if fetch_result["success"]:
        timestamp = datetime.now(timezone.utc)
        output_path = write_output(
            url=url,
            title=fetch_result["title"],
            content=fetch_result["content"],
            note=note,
            timestamp=timestamp
        )
        print(f"✓ Written: {output_path.name}")
    else:
        print(f"✗ Failed: {fetch_result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Single URL mode (from workflow dispatch)
        url = sys.argv[1]
        note = sys.argv[2] if len(sys.argv) > 2 else ""
        fetch_single_url(url, note)
    else:
        # Queue processing mode
        process_queue()
