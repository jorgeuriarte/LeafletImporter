#!/usr/bin/env python3
"""
Check links in posts and add Wayback Machine archived links next to broken ones.

Does NOT replace original links - adds [archived] link beside them.
"""

import json
import re
import time
import requests
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


def extract_links_from_markdown(markdown: str) -> list[dict]:
    """Extract all links with their positions from markdown content."""
    links = []

    # Markdown links: [text](url)
    for match in re.finditer(r'\[([^\]]*)\]\(([^)]+)\)', markdown):
        links.append({
            'type': 'markdown',
            'full_match': match.group(0),
            'text': match.group(1),
            'url': match.group(2),
            'start': match.start(),
            'end': match.end(),
        })

    # Bare URLs: <http://...>
    for match in re.finditer(r'<(https?://[^>]+)>', markdown):
        links.append({
            'type': 'bare',
            'full_match': match.group(0),
            'text': None,
            'url': match.group(1),
            'start': match.start(),
            'end': match.end(),
        })

    return links


def check_link(url: str, timeout: int = 10) -> tuple[str, int | None, str | None]:
    """Check if a URL is accessible."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; LinkChecker/1.0)',
            'Accept': 'text/html,*/*',
        }
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 405:
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
        return (url, resp.status_code, None)
    except requests.exceptions.Timeout:
        return (url, None, "Timeout")
    except requests.exceptions.ConnectionError:
        return (url, None, "Connection error")
    except Exception as e:
        return (url, None, str(e)[:50])


def get_wayback_url(url: str, target_date: str | None = None) -> dict | None:
    """Get archived version from Wayback Machine."""
    try:
        api_url = "https://archive.org/wayback/available"
        params = {"url": url}
        if target_date:
            params["timestamp"] = target_date

        resp = requests.get(api_url, params=params, timeout=10)
        if resp.ok:
            data = resp.json()
            closest = data.get("archived_snapshots", {}).get("closest")
            if closest and closest.get("available"):
                return {
                    "url": closest.get("url"),
                    "timestamp": closest.get("timestamp"),
                }
    except Exception:
        pass
    return None


def process_post(post_dir: Path, post_meta: dict, dry_run: bool = False) -> dict:
    """Process a single post, checking links and adding Wayback alternatives."""
    md_file = post_dir / "post.md"
    if not md_file.exists():
        return {"status": "skipped", "reason": "no markdown"}

    markdown = md_file.read_text(encoding="utf-8")
    links = extract_links_from_markdown(markdown)

    if not links:
        return {"status": "skipped", "reason": "no links"}

    # Get post date for Wayback search
    post_date = post_meta.get("date", "")
    target_date = post_date.replace("-", "") if post_date else None

    results = {
        "title": post_meta.get("title", "Untitled"),
        "date": post_date,
        "total_links": len(links),
        "broken": 0,
        "archived": 0,
        "changes": [],
    }

    # Check each link
    changes_to_make = []

    for link in links:
        url = link["url"]

        # Skip local/relative links and already-archived links
        if not url.startswith(("http://", "https://")) or "web.archive.org" in url:
            continue

        # Check if link is broken
        _, status, error = check_link(url)
        is_broken = status is None or status >= 400

        if is_broken:
            results["broken"] += 1

            # Search Wayback for alternative
            wayback = get_wayback_url(url, target_date)

            if wayback:
                results["archived"] += 1
                wayback_url = wayback["url"]
                timestamp = wayback["timestamp"]

                # Format timestamp nicely
                try:
                    dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
                    date_str = dt.strftime("%Y-%m-%d")
                except:
                    date_str = timestamp[:8]

                # Create the replacement text
                if link["type"] == "markdown":
                    # [text](url) -> [text](url) ([archived YYYY-MM-DD](wayback_url))
                    new_text = f'{link["full_match"]} ([archived {date_str}]({wayback_url}))'
                else:
                    # <url> -> <url> ([archived YYYY-MM-DD](wayback_url))
                    new_text = f'{link["full_match"]} ([archived {date_str}]({wayback_url}))'

                changes_to_make.append({
                    "original": link["full_match"],
                    "replacement": new_text,
                    "url": url,
                    "wayback_url": wayback_url,
                    "wayback_date": date_str,
                })

            # Rate limit Wayback API
            time.sleep(0.3)

    # Apply changes if not dry run
    if changes_to_make and not dry_run:
        new_markdown = markdown
        # Apply changes in reverse order to preserve positions
        for change in sorted(changes_to_make, key=lambda c: markdown.find(c["original"]), reverse=True):
            new_markdown = new_markdown.replace(change["original"], change["replacement"], 1)

        md_file.write_text(new_markdown, encoding="utf-8")
        results["status"] = "updated"
    elif changes_to_make:
        results["status"] = "would_update"
    else:
        results["status"] = "ok"

    results["changes"] = changes_to_make
    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Add Wayback links to broken URLs in posts")
    parser.add_argument("--posts-dir", default="output/posts", help="Posts directory")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--limit", type=int, help="Limit number of posts to process")
    parser.add_argument("--post", help="Process a specific post directory")

    args = parser.parse_args()
    posts_dir = Path(args.posts_dir)

    print("=" * 60)
    print("Add Wayback Archive Links to Broken URLs")
    print("=" * 60)

    if args.dry_run:
        print("[DRY RUN - No changes will be made]")

    # Load posts
    if args.post:
        # Process single post
        post_dir = posts_dir / args.post
        meta_file = post_dir / "metadata.json"
        if meta_file.exists():
            meta = json.load(open(meta_file))
            posts = [meta]
        else:
            print(f"Error: Post not found: {args.post}")
            return
    else:
        index_file = posts_dir / "index.json"
        if not index_file.exists():
            print(f"Error: index.json not found in {posts_dir}")
            return
        with open(index_file) as f:
            index = json.load(f)
        posts = index.get("posts", [])

    if args.limit:
        posts = posts[:args.limit]

    print(f"\nProcessing {len(posts)} posts...\n")

    total_broken = 0
    total_archived = 0
    posts_updated = 0

    for i, post in enumerate(posts, 1):
        post_dir = posts_dir / post["directory"]
        title = post.get("title", "Untitled")[:40]

        print(f"[{i}/{len(posts)}] {title}...")

        result = process_post(post_dir, post, dry_run=args.dry_run)

        if result.get("broken", 0) > 0:
            total_broken += result["broken"]
            total_archived += result.get("archived", 0)

            if result["changes"]:
                posts_updated += 1
                for change in result["changes"]:
                    print(f"    🔗 {change['url'][:50]}...")
                    print(f"       -> archived {change['wayback_date']}")

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Total broken links found: {total_broken}")
    print(f"  Wayback alternatives found: {total_archived}")
    print(f"  Posts {'would be ' if args.dry_run else ''}updated: {posts_updated}")
    print("=" * 60)


if __name__ == "__main__":
    main()
