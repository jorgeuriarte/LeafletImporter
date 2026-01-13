#!/usr/bin/env python3
"""
Check for broken links in posts and find archived versions via Wayback Machine.
"""

import json
import re
import time
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed


def extract_links_from_markdown(markdown: str) -> list[str]:
    """Extract all URLs from markdown content."""
    links = []

    # Markdown links: [text](url)
    md_links = re.findall(r'\[([^\]]*)\]\(([^)]+)\)', markdown)
    links.extend(url for _, url in md_links)

    # Bare URLs: <http://...> or <https://...>
    bare_links = re.findall(r'<(https?://[^>]+)>', markdown)
    links.extend(bare_links)

    # Plain URLs in text
    plain_links = re.findall(r'(?<![(<])(https?://[^\s<>\)]+)', markdown)
    links.extend(plain_links)

    # Deduplicate and filter
    unique_links = []
    seen = set()
    for link in links:
        # Clean up link
        link = link.strip()
        if link and link not in seen:
            # Skip local/relative links
            if link.startswith(('http://', 'https://')):
                seen.add(link)
                unique_links.append(link)

    return unique_links


def check_link(url: str, timeout: int = 10) -> tuple[str, int | None, str | None]:
    """
    Check if a URL is accessible.
    Returns (url, status_code, error_message)
    """
    try:
        # Some sites block requests without proper headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; LinkChecker/1.0)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)

        # Some servers don't support HEAD, try GET
        if resp.status_code == 405:
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)

        return (url, resp.status_code, None)
    except requests.exceptions.Timeout:
        return (url, None, "Timeout")
    except requests.exceptions.ConnectionError as e:
        return (url, None, f"Connection error: {str(e)[:50]}")
    except requests.exceptions.TooManyRedirects:
        return (url, None, "Too many redirects")
    except Exception as e:
        return (url, None, f"Error: {str(e)[:50]}")


def get_wayback_url(url: str, target_date: str | None = None) -> dict | None:
    """
    Get archived version of URL from Wayback Machine.

    Args:
        url: The URL to look up
        target_date: Target date in YYYYMMDD format (optional)

    Returns:
        Dict with 'url', 'timestamp', 'status' or None if not found
    """
    try:
        api_url = "https://archive.org/wayback/available"
        params = {"url": url}

        if target_date:
            params["timestamp"] = target_date

        resp = requests.get(api_url, params=params, timeout=10)

        if resp.ok:
            data = resp.json()
            snapshots = data.get("archived_snapshots", {})
            closest = snapshots.get("closest")

            if closest and closest.get("available"):
                return {
                    "url": closest.get("url"),
                    "timestamp": closest.get("timestamp"),
                    "status": closest.get("status"),
                }
    except Exception as e:
        print(f"    Wayback API error: {e}")

    return None


def scan_posts_for_links(posts_dir: Path) -> list[dict]:
    """Scan all posts and extract links with post metadata."""
    index_file = posts_dir / "index.json"
    if not index_file.exists():
        print(f"Error: index.json not found in {posts_dir}")
        return []

    with open(index_file) as f:
        index = json.load(f)

    all_links = []

    for post in index.get("posts", []):
        post_dir = posts_dir / post["directory"]
        md_file = post_dir / "post.md"

        if not md_file.exists():
            continue

        markdown = md_file.read_text(encoding="utf-8")
        links = extract_links_from_markdown(markdown)

        if links:
            # Get publication date for Wayback search
            pub_date = post.get("date", "")
            target_date = pub_date.replace("-", "") if pub_date else None

            for link in links:
                all_links.append({
                    "url": link,
                    "post_title": post.get("title", "Untitled"),
                    "post_dir": post["directory"],
                    "post_date": pub_date,
                    "target_date": target_date,
                })

    return all_links


def check_links_parallel(links: list[dict], max_workers: int = 10) -> list[dict]:
    """Check multiple links in parallel."""
    results = []
    unique_urls = {}

    # Deduplicate URLs but keep track of all posts that use them
    for link in links:
        url = link["url"]
        if url not in unique_urls:
            unique_urls[url] = link
        else:
            # Keep the earliest date for Wayback search
            if link["target_date"] and (not unique_urls[url]["target_date"] or
                                         link["target_date"] < unique_urls[url]["target_date"]):
                unique_urls[url]["target_date"] = link["target_date"]

    print(f"\nChecking {len(unique_urls)} unique URLs...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(check_link, url): info
            for url, info in unique_urls.items()
        }

        for i, future in enumerate(as_completed(futures), 1):
            info = futures[future]
            url, status, error = future.result()

            info["status"] = status
            info["error"] = error
            info["is_broken"] = status is None or status >= 400

            results.append(info)

            if i % 20 == 0:
                print(f"  Checked {i}/{len(unique_urls)} URLs...")

    return results


def find_wayback_alternatives(broken_links: list[dict]) -> list[dict]:
    """Find Wayback Machine alternatives for broken links."""
    print(f"\nSearching Wayback Machine for {len(broken_links)} broken links...")

    for i, link in enumerate(broken_links, 1):
        url = link["url"]
        target_date = link.get("target_date")

        print(f"  [{i}/{len(broken_links)}] {url[:60]}...")

        wayback = get_wayback_url(url, target_date)

        if wayback:
            link["wayback_url"] = wayback["url"]
            link["wayback_timestamp"] = wayback["timestamp"]
            print(f"    Found: {wayback['timestamp']}")
        else:
            link["wayback_url"] = None
            print(f"    Not found in archive")

        # Rate limiting for Wayback API
        time.sleep(0.5)

    return broken_links


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Check for broken links and find archived versions")
    parser.add_argument("--posts-dir", default="output/posts", help="Posts directory")
    parser.add_argument("--check-only", action="store_true", help="Only check links, don't search Wayback")
    parser.add_argument("--output", default="broken_links_report.json", help="Output report file")
    parser.add_argument("--limit", type=int, help="Limit number of links to check")

    args = parser.parse_args()
    posts_dir = Path(args.posts_dir)

    print("=" * 60)
    print("Broken Link Checker with Wayback Machine Integration")
    print("=" * 60)

    # Scan posts for links
    print("\nScanning posts for links...")
    all_links = scan_posts_for_links(posts_dir)
    print(f"Found {len(all_links)} links across all posts")

    if args.limit:
        all_links = all_links[:args.limit]
        print(f"Limiting to {args.limit} links")

    # Check links
    results = check_links_parallel(all_links)

    # Separate working and broken links
    working_links = [r for r in results if not r["is_broken"]]
    broken_links = [r for r in results if r["is_broken"]]

    print(f"\nResults:")
    print(f"  Working links: {len(working_links)}")
    print(f"  Broken links: {len(broken_links)}")

    # Find Wayback alternatives for broken links
    if broken_links and not args.check_only:
        broken_links = find_wayback_alternatives(broken_links)

        # Count how many have alternatives
        with_alternatives = sum(1 for l in broken_links if l.get("wayback_url"))
        print(f"\n  Found Wayback alternatives: {with_alternatives}/{len(broken_links)}")

    # Generate report
    report = {
        "scan_date": datetime.now().isoformat(),
        "total_links": len(results),
        "working_links": len(working_links),
        "broken_links": len(broken_links),
        "broken_with_wayback": sum(1 for l in broken_links if l.get("wayback_url")),
        "details": {
            "broken": broken_links,
        }
    }

    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nReport saved to: {args.output}")

    # Show sample of broken links
    if broken_links:
        print("\n" + "=" * 60)
        print("Sample of broken links:")
        print("=" * 60)
        for link in broken_links[:10]:
            print(f"\n  URL: {link['url'][:70]}")
            print(f"  Post: {link['post_title'][:40]}")
            print(f"  Date: {link['post_date']}")
            print(f"  Status: {link.get('status') or link.get('error')}")
            if link.get("wayback_url"):
                print(f"  Wayback: {link['wayback_url']}")

        if len(broken_links) > 10:
            print(f"\n  ... and {len(broken_links) - 10} more (see report)")


if __name__ == "__main__":
    main()
