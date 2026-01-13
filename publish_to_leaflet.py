#!/usr/bin/env python3
"""
Publish imported Tumblr posts to Leaflet via AT-Proto.

Reads markdown posts from output/posts/ and publishes them to a Leaflet publication.

Usage:
    python publish_to_leaflet.py --handle your.handle --password app-xxxx
    python publish_to_leaflet.py --credentials .credentials
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


def load_credentials(filepath: str) -> tuple[str, str]:
    """Load credentials from a file."""
    creds = {}
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                creds[key.strip()] = value.strip()
    return creds.get("HANDLE", ""), creds.get("APP_PASSWORD", "")


def create_session(handle: str, password: str) -> dict:
    """Authenticate with AT-Proto and get session tokens."""
    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def parse_markdown_to_blocks(markdown: str) -> list[dict]:
    """
    Parse markdown content into Leaflet block structures.

    Handles:
    - Headers (# ## ###)
    - Code blocks (```)
    - Blockquotes (>)
    - Regular paragraphs
    - Inline formatting (bold, italic, links) as facets
    """
    blocks = []
    lines = markdown.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip empty lines
        if not line.strip():
            i += 1
            continue

        # Code block
        if line.strip().startswith("```"):
            language = line.strip()[3:].strip() or None
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # Skip closing ```

            code_text = "\n".join(code_lines)
            block = {
                "type": "code",
                "text": code_text,
            }
            if language:
                block["language"] = language
            blocks.append(block)
            continue

        # Header
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            level = len(header_match.group(1))
            text = header_match.group(2).strip()
            blocks.append({
                "type": "header",
                "text": text,
                "level": level,
            })
            i += 1
            continue

        # Blockquote (may span multiple lines)
        if line.strip().startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                # Remove the > prefix
                quote_line = re.sub(r"^>\s?", "", lines[i])
                quote_lines.append(quote_line)
                i += 1

            quote_text = "\n".join(quote_lines).strip()
            blocks.append({
                "type": "blockquote",
                "text": quote_text,
            })
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", line.strip()):
            blocks.append({"type": "horizontal_rule"})
            i += 1
            continue

        # Regular paragraph (may span multiple lines until empty line)
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("#", ">", "```", "---", "***", "___")):
            para_lines.append(lines[i])
            i += 1

        para_text = " ".join(para_lines).strip()
        # Handle line breaks (two spaces + newline in markdown)
        para_text = re.sub(r"\s{2,}\n", "\n", para_text)

        if para_text:
            blocks.append({
                "type": "text",
                "text": para_text,
            })

    return blocks


def extract_facets(text: str) -> tuple[str, list[dict]]:
    """
    Extract inline formatting and convert to facets.

    Returns (plain_text, facets).
    Handles: **bold**, *italic*, [link](url)
    """
    facets = []
    plain_text = text

    # Process links first: [text](url)
    link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
    offset_adjustment = 0

    for match in re.finditer(link_pattern, text):
        link_text = match.group(1)
        link_url = match.group(2)

        # Calculate position in the result string
        start_in_original = match.start()
        start_adjusted = start_in_original - offset_adjustment

        # Replace the markdown link with just the text
        old_len = len(match.group(0))
        new_len = len(link_text)

        facets.append({
            "index": {
                "byteStart": start_adjusted,
                "byteEnd": start_adjusted + new_len,
            },
            "features": [{
                "$type": "pub.leaflet.richtext.facet#link",
                "uri": link_url,
            }]
        })

        offset_adjustment += old_len - new_len

    # Remove link markdown syntax but keep text
    plain_text = re.sub(link_pattern, r'\1', plain_text)

    # Process bold: **text** or __text__
    bold_pattern = r'\*\*([^*]+)\*\*|__([^_]+)__'

    # We need to recalculate after link removal
    temp_text = plain_text
    plain_text = ""
    last_end = 0

    for match in re.finditer(bold_pattern, temp_text):
        bold_text = match.group(1) or match.group(2)

        # Add text before match
        plain_text += temp_text[last_end:match.start()]
        start_pos = len(plain_text.encode('utf-8'))

        plain_text += bold_text
        end_pos = len(plain_text.encode('utf-8'))

        facets.append({
            "index": {
                "byteStart": start_pos,
                "byteEnd": end_pos,
            },
            "features": [{
                "$type": "pub.leaflet.richtext.facet#bold",
            }]
        })

        last_end = match.end()

    plain_text += temp_text[last_end:]

    # Process italic: *text* (but not **)
    italic_pattern = r'(?<!\*)\*([^*]+)\*(?!\*)'

    temp_text = plain_text
    plain_text = ""
    last_end = 0

    for match in re.finditer(italic_pattern, temp_text):
        italic_text = match.group(1)

        plain_text += temp_text[last_end:match.start()]
        start_pos = len(plain_text.encode('utf-8'))

        plain_text += italic_text
        end_pos = len(plain_text.encode('utf-8'))

        facets.append({
            "index": {
                "byteStart": start_pos,
                "byteEnd": end_pos,
            },
            "features": [{
                "$type": "pub.leaflet.richtext.facet#italic",
            }]
        })

        last_end = match.end()

    plain_text += temp_text[last_end:]

    return plain_text, facets


def convert_blocks_to_leaflet(blocks: list[dict]) -> list[dict]:
    """Convert parsed blocks to Leaflet block format."""
    leaflet_blocks = []

    for block in blocks:
        if block["type"] == "header":
            plain_text, facets = extract_facets(block["text"])
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.header",
                    "plaintext": plain_text,
                    "level": block.get("level", 1),
                    "facets": facets,
                }
            })

        elif block["type"] == "text":
            plain_text, facets = extract_facets(block["text"])
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.text",
                    "plaintext": plain_text,
                    "facets": facets,
                }
            })

        elif block["type"] == "blockquote":
            plain_text, facets = extract_facets(block["text"])
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.blockquote",
                    "plaintext": plain_text,
                    "facets": facets,
                }
            })

        elif block["type"] == "code":
            leaflet_block = {
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.code",
                    "plaintext": block["text"],
                }
            }
            if block.get("language"):
                leaflet_block["block"]["language"] = block["language"]
            leaflet_blocks.append(leaflet_block)

        elif block["type"] == "horizontal_rule":
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.horizontalRule",
                }
            })

    return leaflet_blocks


def publish_document(
    session: dict,
    publication_uri: str,
    title: str,
    markdown_content: str,
    published_at: str | None = None,
) -> dict:
    """
    Publish a markdown document to Leaflet.

    Returns the created record info (uri, cid).
    """
    did = session["did"]
    access_jwt = session["accessJwt"]

    # Parse markdown to blocks
    blocks = parse_markdown_to_blocks(markdown_content)

    # Skip the first block if it's a header matching the title
    if blocks and blocks[0]["type"] == "header" and blocks[0]["text"].strip() == title.strip():
        blocks = blocks[1:]

    # Convert to Leaflet format
    leaflet_blocks = convert_blocks_to_leaflet(blocks)

    # If no blocks, add a simple text block
    if not leaflet_blocks:
        leaflet_blocks = [{
            "$type": "pub.leaflet.pages.linearDocument#block",
            "block": {
                "$type": "pub.leaflet.blocks.text",
                "plaintext": "(Empty post)",
                "facets": [],
            }
        }]

    # Use provided date or current time
    if published_at:
        # Convert to ISO format if needed
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            pub_date = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except:
            pub_date = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        pub_date = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    record = {
        "$type": "pub.leaflet.document",
        "author": did,
        "title": title,
        "description": "",
        "publication": publication_uri,
        "publishedAt": pub_date,
        "pages": [
            {
                "$type": "pub.leaflet.pages.linearDocument",
                "id": "page-1",
                "blocks": leaflet_blocks,
            }
        ]
    }

    # Generate a TID-like rkey
    rkey = hex(int(time.time() * 1000000))[2:]

    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.repo.putRecord",
        headers={
            "Authorization": f"Bearer {access_jwt}",
            "Content-Type": "application/json",
        },
        json={
            "repo": did,
            "collection": "pub.leaflet.document",
            "rkey": rkey,
            "record": record,
            "validate": False,
        },
    )

    if resp.status_code != 200:
        print(f"Error response: {resp.text}")
        resp.raise_for_status()

    return resp.json()


def publish_posts(
    session: dict,
    publication_uri: str,
    posts_dir: Path,
    limit: int | None = None,
    skip_existing: bool = True,
) -> list[dict]:
    """
    Publish all posts from the output directory.

    Returns list of published post info.
    """
    index_file = posts_dir / "index.json"
    if not index_file.exists():
        print(f"Error: index.json not found in {posts_dir}")
        return []

    with open(index_file) as f:
        index = json.load(f)

    posts = index.get("posts", [])
    if limit:
        posts = posts[:limit]

    # Track published posts
    published_file = posts_dir / "published.json"
    published = {}
    if published_file.exists():
        with open(published_file) as f:
            published = json.load(f)

    results = []

    for i, post_meta in enumerate(posts, 1):
        post_dir = posts_dir / post_meta["directory"]
        md_file = post_dir / "post.md"

        if not md_file.exists():
            print(f"  Skipping (no markdown): {post_meta['directory']}")
            continue

        # Skip if already published
        post_id = post_meta["id"]
        if skip_existing and post_id in published:
            print(f"  [{i}/{len(posts)}] Skipping (already published): {post_meta['title'][:40]}...")
            continue

        title = post_meta.get("title", "Untitled")
        display_title = title[:40] + "..." if len(title) > 40 else title
        print(f"  [{i}/{len(posts)}] Publishing: {display_title}")

        try:
            markdown = md_file.read_text(encoding="utf-8")

            result = publish_document(
                session,
                publication_uri,
                title,
                markdown,
                published_at=post_meta.get("datetime"),
            )

            # Store result
            published[post_id] = {
                "uri": result["uri"],
                "cid": result["cid"],
                "published_at": datetime.now(timezone.utc).isoformat(),
                "title": title,
            }

            results.append({
                "post_id": post_id,
                "title": title,
                **result
            })

            # Save progress after each post
            with open(published_file, "w") as f:
                json.dump(published, f, indent=2)

            # Rate limiting
            time.sleep(0.5)

        except Exception as e:
            print(f"    ERROR: {e}")
            continue

    return results


def main():
    parser = argparse.ArgumentParser(description="Publish Tumblr posts to Leaflet")
    parser.add_argument("--handle", help="Bluesky handle (e.g., user.bsky.social)")
    parser.add_argument("--password", help="Bluesky App Password")
    parser.add_argument("--credentials", default=".credentials", help="Credentials file path")
    parser.add_argument(
        "--publication",
        default="at://did:plc:mkhquxb7mhifi2kfleclinwa/pub.leaflet.publication/3mcd7ta3x3c26",
        help="Publication URI to publish to"
    )
    parser.add_argument(
        "--posts-dir",
        default="output/posts",
        help="Directory containing imported posts"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of posts to publish"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-publish even if already published"
    )

    args = parser.parse_args()

    # Get credentials
    handle = args.handle
    password = args.password

    if not handle or not password:
        cred_file = Path(args.credentials)
        if cred_file.exists():
            handle, password = load_credentials(str(cred_file))
        else:
            print("Error: No credentials provided. Use --handle/--password or --credentials")
            return

    print("=" * 60)
    print("Tumblr to Leaflet Publisher")
    print("=" * 60)

    # Authenticate
    print("\n1. Authenticating...")
    try:
        session = create_session(handle, password)
        print(f"   Authenticated as: {session['did']}")
    except Exception as e:
        print(f"   ERROR: Authentication failed: {e}")
        return

    # Publish posts
    print(f"\n2. Publishing posts from {args.posts_dir}...")
    posts_dir = Path(args.posts_dir)

    results = publish_posts(
        session,
        args.publication,
        posts_dir,
        limit=args.limit,
        skip_existing=not args.force,
    )

    print(f"\n" + "=" * 60)
    print(f"Published {len(results)} posts")
    print("=" * 60)

    # Show publication URL
    pub_parts = args.publication.split("/")
    pub_rkey = pub_parts[-1]
    did = session["did"]
    print(f"\nView publication: https://leaflet.pub/lish/{did}/{pub_rkey}/")


if __name__ == "__main__":
    main()
