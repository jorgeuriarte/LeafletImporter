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
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

# Base32 sortable alphabet used by AT Protocol
B32_CHARSET = "234567abcdefghijklmnopqrstuvwxyz"


def generate_tid() -> str:
    """Generate a proper AT Protocol TID (Timestamp ID)."""
    # TID is 64 bits:
    # - High 53 bits: microseconds since Unix epoch
    # - Low 10 bits: clock identifier (random)
    timestamp_us = int(time.time() * 1_000_000)
    clock_id = int.from_bytes(uuid.uuid4().bytes[:2], 'big') & 0x3FF  # 10 bits

    tid_int = (timestamp_us << 10) | clock_id

    # Encode as base32 (13 characters)
    result = []
    for _ in range(13):
        result.append(B32_CHARSET[tid_int & 0x1F])
        tid_int >>= 5

    return ''.join(reversed(result))


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

        # Unordered list (- or * at start)
        if re.match(r"^[\-\*]\s+", line.strip()):
            list_items = []
            while i < len(lines) and re.match(r"^[\-\*]\s+", lines[i].strip()):
                item_text = re.sub(r"^[\-\*]\s+", "", lines[i].strip())
                list_items.append(item_text)
                i += 1

            blocks.append({
                "type": "unordered_list",
                "items": list_items,
            })
            continue

        # Images in markdown: ![alt](url) - can be multiple per line
        img_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        img_matches = list(re.finditer(img_pattern, line))
        if img_matches:
            for img_match in img_matches:
                blocks.append({
                    "type": "image",
                    "alt": img_match.group(1),
                    "url": img_match.group(2),
                })
            i += 1
            continue

        # Regular paragraph (may span multiple lines until empty line)
        para_lines = []
        while i < len(lines) and lines[i].strip():
            current = lines[i].strip()
            # Stop if we hit a block-level element
            if current.startswith(("#", ">", "```", "---", "***", "___")):
                break
            # Stop if it's a list item (- or * followed by space)
            if re.match(r"^[\-\*]\s+", current):
                break
            # Stop if it's an image
            if re.match(r"^!\[", current):
                break
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
        elif not para_lines:
            # Safety: if we couldn't process this line, skip it to avoid infinite loop
            i += 1

    return blocks


def extract_facets(text: str) -> tuple[str, list[dict]]:
    """
    Extract inline formatting and convert to facets.

    Returns (plain_text, facets).
    Handles: **bold**, *italic*, ~~strikethrough~~, [link](url), `code`
    Supports nested formatting like **bold with *italic***.
    """
    # Strategy: First build the plain text by processing all formats in order.
    # Then, for each format, find its content in the final plain text to get correct positions.

    # Step 1: Remove all formatting markers and track what was formatted
    # Process in order: links, strikethrough, bold, italic, code

    formatting_spans = []  # (start_in_plain, end_in_plain, format_type, extra_data)

    # Helper to process and track
    def process_format(txt, pattern, format_type, extra_fn=None):
        new_text = ""
        last_end = 0
        spans = []

        for match in re.finditer(pattern, txt):
            # Add text before this match
            prefix = txt[last_end:match.start()]
            new_text += prefix

            start_pos = len(new_text)  # Character position

            # Get inner text
            inner = match.group(1)
            if inner is None and match.lastindex >= 2:
                inner = match.group(2)

            new_text += inner
            end_pos = len(new_text)

            extra = extra_fn(match) if extra_fn else None
            spans.append((start_pos, end_pos, inner, format_type, extra))

            last_end = match.end()

        new_text += txt[last_end:]
        return new_text, spans

    current = text

    # Process markdown links [text](url)
    current, link_spans = process_format(
        current,
        r'\[([^\]]+)\]\(([^)]+)\)',
        'link',
        lambda m: m.group(2)
    )

    # Process autolinks <url> - URL becomes both text and link target
    current, autolink_spans = process_format(
        current,
        r'<(https?://[^>]+)>',
        'link',
        lambda m: m.group(1)  # URL is both text and target
    )
    link_spans.extend(autolink_spans)

    # Process strikethrough
    current, strike_spans = process_format(
        current,
        r'~~(.+?)~~',
        'strikethrough'
    )

    # Process bold
    current, bold_spans = process_format(
        current,
        r'\*\*(.+?)\*\*|__(.+?)__',
        'bold'
    )

    # Process italic
    current, italic_spans = process_format(
        current,
        r'\*([^*]+)\*',
        'italic'
    )

    # Process code
    current, code_spans = process_format(
        current,
        r'`([^`]+)`',
        'code'
    )

    plain_text = current

    # Adjust spans for nested formatting
    # When italic markers are removed from inside bold spans, we need to shrink those spans
    # For italic: opening * is at i_start, closing * is at i_end (in the text WITH markers)

    def adjust_outer_spans(outer_spans, inner_spans):
        """Adjust outer span ends when inner formatting markers are removed."""
        adjusted = []
        for o_start, o_end, o_inner, o_fmt, o_extra in outer_spans:
            adjustment = 0
            for i_start, i_end, i_inner, i_fmt, i_extra in inner_spans:
                # Opening marker position is i_start (before the span starts)
                # Closing marker position is i_end (after the inner content)
                opening_marker_pos = i_start
                closing_marker_pos = i_end  # In the text BEFORE italic processing

                # Check if opening marker is inside outer span
                if opening_marker_pos >= o_start and opening_marker_pos < o_end:
                    adjustment += 1

                # Check if closing marker is inside outer span
                # Note: closing marker is at position i_end in PRE-italic text
                # We need to add 1 for the length of inner content to get to closing marker
                closing_actual_pos = i_end + 1  # +1 because i_end is end of content, closing * is after
                if closing_actual_pos > o_start and closing_actual_pos < o_end:
                    adjustment += 1

            adjusted.append((o_start, o_end - adjustment, o_inner, o_fmt, o_extra))
        return adjusted

    # Adjust bold spans for italic removals inside them
    bold_spans = adjust_outer_spans(bold_spans, italic_spans)

    # Strikethrough usually doesn't contain other formatting in our use case
    # but handle it just in case
    strike_spans = adjust_outer_spans(strike_spans, italic_spans)

    # Now convert character positions to byte positions and build facets
    facets = []

    all_spans = link_spans + strike_spans + bold_spans + italic_spans + code_spans

    for char_start, char_end, inner, fmt, extra in all_spans:
        # Convert character positions to byte positions
        byte_start = len(plain_text[:char_start].encode('utf-8'))
        byte_end = len(plain_text[:char_end].encode('utf-8'))

        feature = {"$type": f"pub.leaflet.richtext.facet#{fmt}"}
        if fmt == 'link' and extra:
            feature["uri"] = extra

        facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [feature]
        })

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

        elif block["type"] == "unordered_list":
            list_children = []
            for item in block["items"]:
                plain_text, facets = extract_facets(item)
                list_children.append({
                    "content": {
                        "$type": "pub.leaflet.blocks.text",
                        "plaintext": plain_text,
                        "facets": facets,
                    }
                })

            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.unorderedList",
                    "children": list_children,
                }
            })

        elif block["type"] == "image":
            # Images need special handling - store for later blob upload
            # For now, add a placeholder that will be processed
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.image",
                    "_pending_url": block["url"],
                    "_alt": block.get("alt", ""),
                }
            })

    return leaflet_blocks


def upload_blob(session: dict, image_data: bytes, mime_type: str = "image/jpeg") -> dict:
    """
    Upload an image blob to AT-Proto.

    Returns the blob reference to use in records.
    """
    access_jwt = session["accessJwt"]

    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.repo.uploadBlob",
        headers={
            "Authorization": f"Bearer {access_jwt}",
            "Content-Type": mime_type,
        },
        data=image_data,
    )

    if resp.status_code != 200:
        print(f"Blob upload error: {resp.text}")
        resp.raise_for_status()

    return resp.json()["blob"]


def get_image_dimensions(image_data: bytes) -> tuple[int, int]:
    """Get image dimensions from image data."""
    try:
        # Try to get dimensions from image header
        # PNG: bytes 16-24 contain width/height as 4-byte big-endian
        if image_data[:8] == b'\x89PNG\r\n\x1a\n':
            width = int.from_bytes(image_data[16:20], 'big')
            height = int.from_bytes(image_data[20:24], 'big')
            return width, height

        # JPEG: more complex, look for SOF0 marker
        if image_data[:2] == b'\xff\xd8':
            i = 2
            while i < len(image_data) - 8:
                if image_data[i] == 0xff:
                    marker = image_data[i + 1]
                    if marker in (0xc0, 0xc1, 0xc2):  # SOF markers
                        height = int.from_bytes(image_data[i + 5:i + 7], 'big')
                        width = int.from_bytes(image_data[i + 7:i + 9], 'big')
                        return width, height
                    length = int.from_bytes(image_data[i + 2:i + 4], 'big')
                    i += 2 + length
                else:
                    i += 1
    except Exception:
        pass

    # Default fallback
    return 800, 600


def get_mime_type(url: str, data: bytes) -> str:
    """Determine MIME type from URL or data."""
    url_lower = url.lower()
    if '.png' in url_lower:
        return "image/png"
    elif '.gif' in url_lower:
        return "image/gif"
    elif '.webp' in url_lower:
        return "image/webp"

    # Check magic bytes
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif data[:3] == b'GIF':
        return "image/gif"
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "image/webp"

    return "image/jpeg"


def process_pending_images(session: dict, leaflet_blocks: list[dict], post_dir: Path | None = None) -> list[dict]:
    """
    Process any pending image blocks by uploading blobs.

    Looks for local images first (in post_dir/images/), then tries URL.
    """
    processed_blocks = []

    for block_wrapper in leaflet_blocks:
        block = block_wrapper.get("block", {})

        if block.get("$type") == "pub.leaflet.blocks.image" and "_pending_url" in block:
            url = block["_pending_url"]
            alt = block.get("_alt", "")

            image_data = None

            # Try local file first
            if post_dir:
                # Extract filename from URL
                from urllib.parse import urlparse
                parsed = urlparse(url)
                local_candidates = [
                    post_dir / "images" / Path(parsed.path).name,
                    post_dir / url if not url.startswith("http") else None,
                ]

                for local_path in local_candidates:
                    if local_path and local_path.exists():
                        try:
                            image_data = local_path.read_bytes()
                            print(f"    Loaded local image: {local_path.name}")
                            break
                        except Exception:
                            pass

            # Try URL if no local file
            if not image_data and url.startswith("http"):
                try:
                    resp = requests.get(url, timeout=30, headers={
                        "User-Agent": "Mozilla/5.0 (compatible; LeafletImporter/1.0)"
                    })
                    resp.raise_for_status()
                    image_data = resp.content
                    print(f"    Downloaded image from URL")
                except Exception as e:
                    print(f"    Failed to download image: {e}")

            if image_data and len(image_data) < 1000000:  # Max 1MB
                try:
                    mime_type = get_mime_type(url, image_data)
                    width, height = get_image_dimensions(image_data)

                    blob = upload_blob(session, image_data, mime_type)

                    processed_blocks.append({
                        "$type": "pub.leaflet.pages.linearDocument#block",
                        "block": {
                            "$type": "pub.leaflet.blocks.image",
                            "image": blob,
                            "alt": alt,
                            "aspectRatio": {
                                "width": width,
                                "height": height,
                            }
                        }
                    })
                    continue
                except Exception as e:
                    print(f"    Failed to upload image blob: {e}")

            # If image failed, skip it or add placeholder text
            print(f"    Skipping image: {url[:50]}...")
            continue

        processed_blocks.append(block_wrapper)

    return processed_blocks


def publish_document(
    session: dict,
    publication_uri: str,
    title: str,
    markdown_content: str,
    published_at: str | None = None,
    post_dir: Path | None = None,
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

    # Process any pending images (upload blobs)
    leaflet_blocks = process_pending_images(session, leaflet_blocks, post_dir)

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
        "tags": [],  # Required for Leaflet UI compatibility
        "description": "",
        "publication": publication_uri,
        "publishedAt": pub_date,
        "pages": [
            {
                "$type": "pub.leaflet.pages.linearDocument",
                "id": str(uuid.uuid4()),  # UUID format for Leaflet UI compatibility
                "blocks": leaflet_blocks,
            }
        ]
    }

    # Generate a proper TID rkey (AT Protocol format)
    rkey = generate_tid()

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
                post_dir=post_dir,
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
