#!/usr/bin/env python3
"""
Test script to publish a document directly to AT-Proto (Leaflet format).

This bypasses Leaflet's UI and publishes directly to your PDS.
Requires Bluesky App Password for authentication.

Usage:
    python test_atproto_publish.py --handle your.handle --password app-xxxx-xxxx

Your publication: https://leaflet.pub/lish/did:plc:mkhquxb7mhifi2kfleclinwa/3mcd7ta3x3c26/
"""

import argparse
import json
import requests
from datetime import datetime, timezone


def create_session(handle: str, password: str) -> dict:
    """Authenticate with AT-Proto and get session tokens."""
    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def create_simple_document(
    session: dict,
    publication_uri: str,
    title: str,
    content: str,
) -> dict:
    """
    Create a simple Leaflet document with one text block.

    Returns the created record info (uri, cid).
    """
    did = session["did"]
    access_jwt = session["accessJwt"]

    # Create a simple document with one page and one text block
    # This follows the pub.leaflet.document lexicon
    record = {
        "$type": "pub.leaflet.document",
        "author": did,
        "title": title,
        "description": "",
        "publication": publication_uri,
        "publishedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pages": [
            {
                "$type": "pub.leaflet.pages.linearDocument",
                "id": "page-1",
                "blocks": [
                    {
                        "$type": "pub.leaflet.pages.linearDocument#block",
                        "block": {
                            "$type": "pub.leaflet.blocks.text",
                            "plaintext": content,
                            "facets": [],
                        }
                    }
                ]
            }
        ]
    }

    # Generate a TID-like rkey (timestamp-based ID)
    import time
    rkey = hex(int(time.time() * 1000000))[2:]  # Simple timestamp-based key

    # Create the record using putRecord
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
            "validate": False,  # Leaflet lexicons may not be published yet
        },
    )

    if resp.status_code != 200:
        print(f"Error response: {resp.text}")
        resp.raise_for_status()

    return resp.json()


def create_document_with_blocks(
    session: dict,
    publication_uri: str,
    title: str,
    blocks: list[dict],
) -> dict:
    """
    Create a Leaflet document with multiple blocks.

    blocks should be a list of block dicts, e.g.:
    [
        {"type": "header", "text": "My Header", "level": 1},
        {"type": "text", "text": "Paragraph content here"},
        {"type": "blockquote", "text": "A quote"},
    ]
    """
    did = session["did"]
    access_jwt = session["accessJwt"]

    # Convert simple blocks to Leaflet format
    leaflet_blocks = []
    for block in blocks:
        if block["type"] == "header":
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.header",
                    "plaintext": block["text"],
                    "level": block.get("level", 1),
                    "facets": [],
                }
            })
        elif block["type"] == "text":
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.text",
                    "plaintext": block["text"],
                    "facets": [],
                }
            })
        elif block["type"] == "blockquote":
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.blockquote",
                    "plaintext": block["text"],
                    "facets": [],
                }
            })
        elif block["type"] == "code":
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.code",
                    "plaintext": block["text"],
                    "language": block.get("language"),
                }
            })
        elif block["type"] == "horizontal_rule":
            leaflet_blocks.append({
                "$type": "pub.leaflet.pages.linearDocument#block",
                "block": {
                    "$type": "pub.leaflet.blocks.horizontalRule",
                }
            })

    record = {
        "$type": "pub.leaflet.document",
        "author": did,
        "title": title,
        "description": "",
        "publication": publication_uri,
        "publishedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pages": [
            {
                "$type": "pub.leaflet.pages.linearDocument",
                "id": "page-1",
                "blocks": leaflet_blocks,
            }
        ]
    }

    import time
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


def main():
    parser = argparse.ArgumentParser(description="Test publishing to AT-Proto/Leaflet")
    parser.add_argument("--handle", required=True, help="Bluesky handle (e.g., user.bsky.social)")
    parser.add_argument("--password", required=True, help="Bluesky App Password")
    parser.add_argument(
        "--publication",
        default="at://did:plc:mkhquxb7mhifi2kfleclinwa/pub.leaflet.publication/3mcd7ta3x3c26",
        help="Publication URI to publish to"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("AT-Proto / Leaflet Publishing Test")
    print("=" * 60)

    # Step 1: Authenticate
    print("\n1. Authenticating with Bluesky...")
    try:
        session = create_session(args.handle, args.password)
        print(f"   Authenticated as: {session['did']}")
    except Exception as e:
        print(f"   ERROR: Authentication failed: {e}")
        return

    # Step 2: Create a simple test document
    print("\n2. Creating test document...")
    try:
        result = create_simple_document(
            session,
            args.publication,
            title="Test Import from Python",
            content="This is a test document created directly via AT-Proto API. If you see this, the import script is working!",
        )
        print(f"   SUCCESS!")
        print(f"   URI: {result['uri']}")
        print(f"   CID: {result['cid']}")

        # Extract leaflet URL
        # at://did:plc:xxx/pub.leaflet.document/rkey -> leaflet.pub/lish/did:plc:xxx/rkey
        uri_parts = result['uri'].replace("at://", "").split("/")
        did = uri_parts[0]
        rkey = uri_parts[2]
        leaflet_url = f"https://leaflet.pub/lish/{did}/{args.publication.split('/')[-1]}/{rkey}"
        print(f"   Leaflet URL: {leaflet_url}")

    except Exception as e:
        print(f"   ERROR: {e}")
        return

    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
