#!/usr/bin/env python3
"""
RSS to Markdown Importer

Imports posts from an RSS feed and converts them to:
- Markdown files with content
- JSON metadata files
- Downloaded images with updated references
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from slugify import slugify

# Configuration
DEFAULT_RSS_URL = "https://blog.omelas.net/rss"
OUTPUT_DIR = Path("output/posts")
IMAGES_SUBDIR = "images"


def fetch_rss(url: str) -> dict:
    """Fetch and parse RSS feed."""
    print(f"Fetching RSS from: {url}")
    feed = feedparser.parse(url)

    if feed.bozo and feed.bozo_exception:
        print(f"Warning: RSS parsing issue: {feed.bozo_exception}")

    print(f"Found {len(feed.entries)} entries")
    return feed


def extract_images_from_html(html: str, base_url: str = "") -> list[dict]:
    """Extract all image URLs from HTML content."""
    soup = BeautifulSoup(html, "html.parser")
    images = []

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            # Handle relative URLs
            if base_url and not src.startswith(("http://", "https://")):
                src = urljoin(base_url, src)

            images.append({
                "original_url": src,
                "alt": img.get("alt", ""),
            })

    return images


def download_image(url: str, dest_dir: Path) -> tuple[str, bool]:
    """
    Download an image and return the local filename.
    Returns (filename, success).
    """
    try:
        # Generate a unique filename based on URL hash + original extension
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix or ".jpg"
        # Clean extension
        ext = ext.split("?")[0][:10]  # Remove query params, limit length
        if not ext.startswith("."):
            ext = ".jpg"

        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        filename = f"{url_hash}{ext}"
        filepath = dest_dir / filename

        # Skip if already downloaded
        if filepath.exists():
            print(f"  - Already downloaded: {filename}")
            return filename, True

        print(f"  - Downloading: {url[:60]}...")
        response = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; RSSImporter/1.0)"
        })
        response.raise_for_status()

        dest_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(response.content)
        print(f"    Saved as: {filename}")
        return filename, True

    except Exception as e:
        print(f"    Failed to download {url}: {e}")
        return "", False


def replace_images_in_html(html: str, image_map: dict[str, str]) -> str:
    """Replace image URLs in HTML with local paths."""
    soup = BeautifulSoup(html, "html.parser")

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src and src in image_map:
            local_path = f"{IMAGES_SUBDIR}/{image_map[src]}"
            img["src"] = local_path
            if img.get("data-src"):
                del img["data-src"]

    return str(soup)


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown."""
    # Configure markdownify for clean output
    markdown = md(
        html,
        heading_style="atx",
        bullets="-",
        strip=["script", "style"],
    )

    # Clean up excessive whitespace
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = markdown.strip()

    return markdown


def parse_date(date_str: str) -> datetime:
    """Parse RSS date string to datetime."""
    try:
        # feedparser provides parsed date as time struct
        from time import mktime
        return datetime.fromtimestamp(mktime(feedparser.parse("").entries[0].published_parsed if False else None))
    except:
        pass

    # Try common formats
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Default to now if parsing fails
    return datetime.now()


def process_entry(entry: dict, output_dir: Path) -> dict:
    """Process a single RSS entry."""
    # Extract basic metadata
    title = entry.get("title", "Untitled")
    link = entry.get("link", "")
    guid = entry.get("id", link)
    pub_date = entry.get("published", entry.get("updated", ""))
    categories = [tag.term for tag in entry.get("tags", [])]

    # Parse date
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        from time import mktime
        dt = datetime.fromtimestamp(mktime(entry.published_parsed))
    else:
        dt = parse_date(pub_date)

    date_str = dt.strftime("%Y-%m-%d")

    # Create slug from title
    slug = slugify(title, max_length=50)
    if not slug:
        slug = hashlib.md5(guid.encode()).hexdigest()[:10]

    # Create post directory
    post_dir_name = f"{date_str}-{slug}"
    post_dir = output_dir / post_dir_name
    post_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nProcessing: {title}")
    print(f"  Directory: {post_dir_name}")

    # Get content (try different fields)
    content_html = ""
    if "content" in entry and entry.content:
        content_html = entry.content[0].get("value", "")
    if not content_html:
        content_html = entry.get("summary", entry.get("description", ""))

    # Extract and download images
    images = extract_images_from_html(content_html, link)
    image_map = {}

    if images:
        print(f"  Found {len(images)} images")
        images_dir = post_dir / IMAGES_SUBDIR

        for img_info in images:
            url = img_info["original_url"]
            filename, success = download_image(url, images_dir)
            if success:
                image_map[url] = filename
                img_info["local_file"] = filename

    # Replace image URLs in HTML before converting to markdown
    if image_map:
        content_html = replace_images_in_html(content_html, image_map)

    # Convert to markdown
    markdown_content = html_to_markdown(content_html)

    # Create frontmatter-style header
    full_markdown = f"""# {title}

{markdown_content}
"""

    # Write markdown file
    md_file = post_dir / "post.md"
    md_file.write_text(full_markdown, encoding="utf-8")
    print(f"  Written: post.md")

    # Create metadata JSON
    metadata = {
        "title": title,
        "slug": slug,
        "date": date_str,
        "datetime": dt.isoformat(),
        "link": link,
        "guid": guid,
        "categories": categories,
        "images": images,
        "directory": post_dir_name,
    }

    json_file = post_dir / "metadata.json"
    json_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Written: metadata.json")

    return metadata


def import_rss(rss_url: str, output_dir: Path) -> list[dict]:
    """Main import function."""
    output_dir.mkdir(parents=True, exist_ok=True)

    feed = fetch_rss(rss_url)

    results = []
    for entry in feed.entries:
        try:
            metadata = process_entry(entry, output_dir)
            results.append(metadata)
        except Exception as e:
            print(f"Error processing entry: {e}")
            import traceback
            traceback.print_exc()

    # Write index file
    index_file = output_dir / "index.json"
    index_data = {
        "source": rss_url,
        "imported_at": datetime.now().isoformat(),
        "total_posts": len(results),
        "posts": results,
    }
    index_file.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten index: {index_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Import RSS feed to Markdown")
    parser.add_argument(
        "--url",
        default=DEFAULT_RSS_URL,
        help=f"RSS feed URL (default: {DEFAULT_RSS_URL})"
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_DIR),
        help=f"Output directory (default: {OUTPUT_DIR})"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("RSS to Markdown Importer")
    print("=" * 60)

    results = import_rss(args.url, Path(args.output))

    print("\n" + "=" * 60)
    print(f"Import complete: {len(results)} posts processed")
    print("=" * 60)


if __name__ == "__main__":
    main()
