#!/usr/bin/env python3
"""
Tumblr to Markdown Importer

Imports ALL posts from a Tumblr blog using the v1 API and converts them to:
- Markdown files with content
- JSON metadata files
- Downloaded images with updated references
"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from slugify import slugify

# Configuration
DEFAULT_BLOG = "blog.omelas.net"
OUTPUT_DIR = Path("output/posts")
IMAGES_SUBDIR = "images"
POSTS_PER_REQUEST = 50  # Max allowed by API


def fetch_all_posts(blog: str) -> list[dict]:
    """Fetch all posts from a Tumblr blog using the v1 API."""
    api_url = f"https://{blog}/api/read/json"
    all_posts = []
    start = 0

    print(f"Fetching posts from: {blog}")

    # First request to get total
    response = requests.get(
        api_url,
        params={"num": POSTS_PER_REQUEST, "start": 0},
        headers={"User-Agent": "Mozilla/5.0 (compatible; TumblrImporter/1.0)"},
        timeout=30,
    )
    response.raise_for_status()

    data = parse_jsonp_response(response.text)
    total_posts = data["posts-total"]
    print(f"Total posts available: {total_posts}")

    all_posts.extend(data["posts"])
    start += POSTS_PER_REQUEST

    # Fetch remaining posts
    while start < total_posts:
        print(f"  Fetching posts {start}-{min(start + POSTS_PER_REQUEST, total_posts)}...")
        response = requests.get(
            api_url,
            params={"num": POSTS_PER_REQUEST, "start": start},
            headers={"User-Agent": "Mozilla/5.0 (compatible; TumblrImporter/1.0)"},
            timeout=30,
        )
        response.raise_for_status()

        data = parse_jsonp_response(response.text)
        all_posts.extend(data["posts"])
        start += POSTS_PER_REQUEST

        # Be nice to the server
        time.sleep(0.5)

    print(f"Fetched {len(all_posts)} posts")
    return all_posts


def parse_jsonp_response(text: str) -> dict:
    """Parse Tumblr's JSONP response format."""
    match = re.match(r"var tumblr_api_read = (.+);$", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    raise ValueError("Invalid JSONP response")


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
    """Download an image and return the local filename."""
    try:
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix or ".jpg"
        ext = ext.split("?")[0][:10]
        if not ext.startswith("."):
            ext = ".jpg"

        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        filename = f"{url_hash}{ext}"
        filepath = dest_dir / filename

        if filepath.exists():
            return filename, True

        response = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (compatible; TumblrImporter/1.0)"
        })
        response.raise_for_status()

        dest_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(response.content)
        return filename, True

    except Exception as e:
        print(f"    Failed to download {url[:50]}: {e}")
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


def clean_html(html: str) -> str:
    """Remove unwanted elements from HTML before conversion."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style tags
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Remove Google Ads and tracking elements
    for tag in soup.find_all(text=re.compile(r"google_ad_|google_color_|google_ui_")):
        if tag.parent:
            tag.parent.decompose()

    return str(soup)


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown."""
    # Clean HTML first
    html = clean_html(html)

    markdown = md(
        html,
        heading_style="atx",
        bullets="-",
        strip=["script", "style"],
    )

    # Clean up excessive whitespace
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = markdown.strip()

    # Convert escaped asterisks to emphasis
    markdown = re.sub(r"\\\*([^*\n]+)\\\*", r"*\1*", markdown)

    # Remove any remaining Google Ads code that leaked through
    markdown = re.sub(r"google_ad_\w+\s*=\s*[\"'][^\"']*[\"'];?\n?", "", markdown)
    markdown = re.sub(r"google_\w+\s*=\s*[\"']?[^;\"'\n]*[\"']?;?\n?", "", markdown)

    # Clean up resulting whitespace again
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = markdown.strip()

    return markdown


def get_post_content(post: dict) -> tuple[str, str]:
    """Extract title and HTML content from a Tumblr post based on type."""
    post_type = post.get("type", "regular")

    if post_type == "regular":
        title = post.get("regular-title", "")
        body = post.get("regular-body", "")
    elif post_type == "quote":
        title = ""
        quote_text = post.get("quote-text", "")
        quote_source = post.get("quote-source", "")
        body = f"<blockquote>{quote_text}</blockquote>\n<p>— {quote_source}</p>"
    elif post_type == "link":
        title = post.get("link-text", post.get("link-url", ""))
        link_url = post.get("link-url", "")
        link_desc = post.get("link-description", "")
        body = f'<p><a href="{link_url}">{title}</a></p>\n{link_desc}'
    elif post_type == "photo":
        title = ""
        caption = post.get("photo-caption", "")
        photos = post.get("photos", [])
        if photos:
            body = ""
            for photo in photos:
                url = photo.get("photo-url-1280", photo.get("photo-url-500", ""))
                body += f'<figure><img src="{url}" /></figure>\n'
            body += caption
        else:
            photo_url = post.get("photo-url-1280", post.get("photo-url-500", ""))
            body = f'<figure><img src="{photo_url}" /></figure>\n{caption}'
    elif post_type == "video":
        title = ""
        caption = post.get("video-caption", "")
        player = post.get("video-player", "")
        body = f"{player}\n{caption}"
    elif post_type == "audio":
        title = ""
        caption = post.get("audio-caption", "")
        player = post.get("audio-player", "")
        body = f"{player}\n{caption}"
    elif post_type == "conversation":
        title = post.get("conversation-title", "")
        lines = post.get("conversation", [])
        body = "<dl>\n"
        for line in lines:
            body += f"<dt>{line.get('label', '')}</dt><dd>{line.get('phrase', '')}</dd>\n"
        body += "</dl>"
    else:
        title = post.get("regular-title", "")
        body = post.get("regular-body", str(post))

    # Use slug as fallback title
    if not title:
        title = post.get("slug", "").replace("-", " ").title()

    return title, body


def process_post(post: dict, output_dir: Path) -> dict:
    """Process a single Tumblr post."""
    post_id = post.get("id", "")
    post_url = post.get("url-with-slug", post.get("url", ""))
    post_type = post.get("type", "regular")
    tags = post.get("tags", [])

    # Parse date from unix timestamp
    unix_ts = post.get("unix-timestamp", 0)
    if unix_ts:
        dt = datetime.fromtimestamp(int(unix_ts))
    else:
        # Fallback to date-gmt string
        date_gmt = post.get("date-gmt", "")
        try:
            dt = datetime.strptime(date_gmt, "%Y-%m-%d %H:%M:%S GMT")
        except ValueError:
            dt = datetime.now()

    date_str = dt.strftime("%Y-%m-%d")

    # Get title and content
    title, content_html = get_post_content(post)

    # Create slug
    slug = post.get("slug", "")
    if not slug:
        slug = slugify(title, max_length=50) if title else hashlib.md5(post_id.encode()).hexdigest()[:10]

    # Create post directory
    post_dir_name = f"{date_str}-{slug}"
    post_dir = output_dir / post_dir_name

    # Skip if already processed
    if (post_dir / "post.md").exists():
        print(f"  Skipping (exists): {post_dir_name}")
        return None

    post_dir.mkdir(parents=True, exist_ok=True)

    display_title = title[:50] + "..." if len(title) > 50 else title
    print(f"Processing: [{post_type}] {display_title or slug}")

    # Extract and download images
    images = extract_images_from_html(content_html, post_url)
    image_map = {}

    if images:
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

    # Create full markdown with title
    if title:
        full_markdown = f"# {title}\n\n{markdown_content}\n"
    else:
        full_markdown = f"{markdown_content}\n"

    # Write markdown file
    md_file = post_dir / "post.md"
    md_file.write_text(full_markdown, encoding="utf-8")

    # Create metadata JSON
    metadata = {
        "id": post_id,
        "title": title,
        "slug": slug,
        "type": post_type,
        "date": date_str,
        "datetime": dt.isoformat(),
        "unix_timestamp": unix_ts,
        "link": post_url,
        "tags": tags,
        "images": images,
        "directory": post_dir_name,
    }

    json_file = post_dir / "metadata.json"
    json_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    return metadata


def import_tumblr(blog: str, output_dir: Path) -> list[dict]:
    """Main import function."""
    output_dir.mkdir(parents=True, exist_ok=True)

    posts = fetch_all_posts(blog)

    results = []
    skipped = 0
    for i, post in enumerate(posts, 1):
        try:
            metadata = process_post(post, output_dir)
            if metadata:
                results.append(metadata)
            else:
                skipped += 1

            # Progress update every 25 posts
            if i % 25 == 0:
                print(f"  Progress: {i}/{len(posts)} posts processed...")

        except Exception as e:
            print(f"Error processing post {post.get('id', '?')}: {e}")
            import traceback
            traceback.print_exc()

    # Write index file
    index_file = output_dir / "index.json"
    index_data = {
        "source": f"https://{blog}",
        "imported_at": datetime.now().isoformat(),
        "total_posts": len(results),
        "skipped": skipped,
        "posts": sorted(results, key=lambda x: x["datetime"], reverse=True),
    }
    index_file.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten index: {index_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Import Tumblr blog to Markdown")
    parser.add_argument(
        "--blog",
        default=DEFAULT_BLOG,
        help=f"Tumblr blog domain (default: {DEFAULT_BLOG})"
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_DIR),
        help=f"Output directory (default: {OUTPUT_DIR})"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Tumblr to Markdown Importer")
    print("=" * 60)

    results = import_tumblr(args.blog, Path(args.output))

    print("\n" + "=" * 60)
    print(f"Import complete: {len(results)} posts processed")
    print("=" * 60)


if __name__ == "__main__":
    main()
