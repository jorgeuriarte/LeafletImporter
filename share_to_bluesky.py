#!/usr/bin/env python3
"""
Share a Leaflet document to Bluesky.

Creates a Bluesky post with an external embed (link card) pointing to the document.
Optionally updates the Leaflet document with a reference to the Bluesky post.

Usage:
    python share_to_bluesky.py --rkey 3mdwy2igiiovt --text "Check out my new post!"
    python share_to_bluesky.py --rkey 3mdwy2igiiovt --text "New post!" --image cover.jpg
    python share_to_bluesky.py --rkey 3mdwy2igiiovt --text "New post!" --dry-run
"""

import argparse
import requests
import sys
import time
import random
import mimetypes
from pathlib import Path

# =============================================================================
# AT Protocol Helpers
# =============================================================================

def load_credentials(path='.credentials'):
    """Load credentials from .credentials file"""
    creds = {}
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    creds[key] = value
    except FileNotFoundError:
        print(f"Error: {path} not found.")
        sys.exit(1)

    if 'HANDLE' not in creds or 'APP_PASSWORD' not in creds:
        print("Error: .credentials must contain HANDLE and APP_PASSWORD")
        sys.exit(1)

    return creds

def resolve_pds(handle):
    """Resolve handle to DID and PDS endpoint"""
    response = requests.get(
        f"https://bsky.social/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": handle}
    )
    if response.status_code != 200:
        print(f"Failed to resolve handle: {response.text}")
        sys.exit(1)

    did = response.json()['did']

    response = requests.get(f"https://plc.directory/{did}")
    if response.status_code != 200:
        print(f"Failed to get DID document: {response.text}")
        sys.exit(1)

    pds = None
    for service in response.json().get('service', []):
        if service['type'] == 'AtprotoPersonalDataServer':
            pds = service['serviceEndpoint']
            break

    if not pds:
        print("Failed to find PDS in DID document")
        sys.exit(1)

    return did, pds

def create_session(pds, handle, password):
    """Create an authenticated session"""
    response = requests.post(f"{pds}/xrpc/com.atproto.server.createSession", json={
        "identifier": handle,
        "password": password
    })

    if response.status_code != 200:
        print(f"Authentication failed: {response.text}")
        sys.exit(1)

    return response.json()

def generate_tid():
    """Generate a valid AT Protocol TID"""
    B32_CHARSET = "234567abcdefghijklmnopqrstuvwxyz"
    timestamp_us = int(time.time() * 1_000_000)
    clock_id = random.randint(0, 1023)
    tid_int = (timestamp_us << 10) | clock_id

    tid = ""
    for _ in range(13):
        tid = B32_CHARSET[tid_int & 0x1F] + tid
        tid_int >>= 5

    return tid

def get_record(pds, did, collection, rkey):
    """Get a record from the PDS"""
    response = requests.get(f"{pds}/xrpc/com.atproto.repo.getRecord", params={
        "repo": did,
        "collection": collection,
        "rkey": rkey
    })
    if response.status_code == 200:
        return response.json()
    return None

def put_record(pds, session, collection, rkey, record):
    """Create or update a record"""
    response = requests.post(
        f"{pds}/xrpc/com.atproto.repo.putRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={
            "repo": session['did'],
            "collection": collection,
            "rkey": rkey,
            "record": record,
            "validate": False
        }
    )
    if response.status_code != 200:
        print(f"Failed to update record: {response.text}")
        return None
    return response.json()

def upload_blob(pds, session, data, mime_type):
    """Upload a blob to the PDS"""
    response = requests.post(
        f"{pds}/xrpc/com.atproto.repo.uploadBlob",
        headers={
            "Authorization": f"Bearer {session['accessJwt']}",
            "Content-Type": mime_type
        },
        data=data
    )
    if response.status_code != 200:
        print(f"Failed to upload blob: {response.text}")
        return None
    return response.json()

def create_post(pds, session, text, embed=None, facets=None):
    """Create a Bluesky post"""
    rkey = generate_tid()

    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }

    if embed:
        record["embed"] = embed

    if facets:
        record["facets"] = facets

    response = requests.post(
        f"{pds}/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={
            "repo": session['did'],
            "collection": "app.bsky.feed.post",
            "rkey": rkey,
            "record": record
        }
    )

    if response.status_code != 200:
        print(f"Failed to create post: {response.text}")
        return None

    return response.json()

# =============================================================================
# Document helpers
# =============================================================================

def detect_document_format(pds, did, rkey):
    """Detect if document is in old or new format"""
    record = get_record(pds, did, "site.standard.document", rkey)
    if record:
        return "site.standard.document", record

    record = get_record(pds, did, "pub.leaflet.document", rkey)
    if record:
        return "pub.leaflet.document", record

    return None, None

def get_document_url(did, collection, rkey, record):
    """Get the public URL for a document"""
    # Try to extract from site field for custom domain
    site = record.get('site', record.get('publication', ''))
    pub_rkey = site.split('/')[-1] if site else None

    # Default to leaflet.pub URL
    return f"https://leaflet.pub/lish/{did}/{pub_rkey}/{rkey}"

def resolve_handle(handle):
    """Resolve a Bluesky handle to a DID"""
    try:
        response = requests.get(
            f"https://bsky.social/xrpc/com.atproto.identity.resolveHandle",
            params={"handle": handle},
            timeout=5
        )
        if response.status_code == 200:
            return response.json().get('did')
    except:
        pass
    return None

def parse_facets(text, resolve_mentions=True):
    """
    Parse text for URLs, mentions (@handle), and hashtags (#tag).
    Returns the facets array for rich text.

    If resolve_mentions is True, will resolve @handles to DIDs for proper linking.
    """
    import re
    facets = []

    # Find URLs in text
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    for match in re.finditer(url_pattern, text):
        url = match.group()
        start = len(text[:match.start()].encode('utf-8'))
        end = len(text[:match.end()].encode('utf-8'))

        facets.append({
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}]
        })

    # Find mentions (@handle.domain or @handle)
    mention_pattern = r'@([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}|@[a-zA-Z0-9_]+'
    for match in re.finditer(mention_pattern, text):
        handle = match.group()[1:]  # Remove @ prefix
        start = len(text[:match.start()].encode('utf-8'))
        end = len(text[:match.end()].encode('utf-8'))

        if resolve_mentions:
            did = resolve_handle(handle)
            if did:
                facets.append({
                    "index": {"byteStart": start, "byteEnd": end},
                    "features": [{"$type": "app.bsky.richtext.facet#mention", "did": did}]
                })
                print(f"   Resolved @{handle} -> {did}")
            else:
                print(f"   WARNING: Could not resolve @{handle}")

    # Find hashtags (#tag)
    hashtag_pattern = r'#[a-zA-Z][a-zA-Z0-9_]*'
    for match in re.finditer(hashtag_pattern, text):
        tag = match.group()[1:]  # Remove # prefix
        start = len(text[:match.start()].encode('utf-8'))
        end = len(text[:match.end()].encode('utf-8'))

        facets.append({
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]
        })

    return facets

# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Share a Leaflet document to Bluesky",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --rkey 3mdwy2igiiovt --text "Check out my new blog post!"
  %(prog)s --rkey 3mdwy2igiiovt --text "New post!" --image cover.jpg
  %(prog)s --rkey 3mdwy2igiiovt --text "Testing" --dry-run
  %(prog)s --rkey 3mdwy2igiiovt --text "Post!" --no-update  # Don't update document
        """
    )
    parser.add_argument('--rkey', required=True, help='Document rkey to share')
    parser.add_argument('--text', required=True, help='Text for the Bluesky post (max 300 chars)')
    parser.add_argument('--image', help='Image file for the link card thumbnail')
    parser.add_argument('--no-update', action='store_true',
                        help='Do not update the document with bskyPostRef')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--credentials', default='.credentials',
                        help='Path to credentials file')

    args = parser.parse_args()

    if len(args.text) > 300:
        print(f"Error: Text is {len(args.text)} chars, max is 300")
        sys.exit(1)

    print("=" * 60)
    print("Share Leaflet Document to Bluesky")
    print("=" * 60)

    if args.dry_run:
        print("[DRY RUN MODE]")

    # Authenticate
    creds = load_credentials(args.credentials)
    handle = creds['HANDLE']

    print(f"\n1. Authenticating as {handle}...")
    did, pds = resolve_pds(handle)
    session = create_session(pds, handle, creds['APP_PASSWORD'])
    print(f"   Authenticated!")

    # Find document
    print(f"\n2. Finding document {args.rkey}...")
    collection, doc_response = detect_document_format(pds, did, args.rkey)

    if not collection:
        print(f"   ERROR: Document not found")
        sys.exit(1)

    record = doc_response['value']
    title = record.get('title', 'Untitled')
    description = record.get('description', '')[:300] if record.get('description') else ''
    doc_url = get_document_url(did, collection, args.rkey, record)

    print(f"   Title: {title}")
    print(f"   URL: {doc_url}")

    # Prepare embed
    print(f"\n3. Preparing Bluesky post...")
    print(f"   Text: {args.text[:50]}{'...' if len(args.text) > 50 else ''}")

    thumb = None
    if args.image:
        image_path = Path(args.image)
        if not image_path.exists():
            print(f"   WARNING: Image file not found: {args.image}")
        else:
            mime_type, _ = mimetypes.guess_type(args.image)
            if not mime_type or not mime_type.startswith('image/'):
                mime_type = 'image/jpeg'

            print(f"   Uploading image: {args.image}")
            if not args.dry_run:
                with open(args.image, 'rb') as f:
                    blob_response = upload_blob(pds, session, f.read(), mime_type)
                if blob_response:
                    thumb = blob_response['blob']
                    print(f"   Image uploaded!")

    embed = {
        "$type": "app.bsky.embed.external",
        "external": {
            "uri": doc_url,
            "title": title[:300],
            "description": description[:1000],
        }
    }

    if thumb:
        embed["external"]["thumb"] = thumb

    # Parse facets (auto-detect links in text)
    facets = parse_facets(args.text)

    if args.dry_run:
        print(f"\n[DRY RUN] Would create Bluesky post:")
        print(f"   Text: {args.text}")
        print(f"   Embed URL: {doc_url}")
        print(f"   Embed title: {title}")
        if not args.no_update:
            print(f"[DRY RUN] Would update document with bskyPostRef")
        print("\nNo changes made.")
        return

    # Create the post
    print(f"\n4. Creating Bluesky post...")
    post_result = create_post(pds, session, args.text, embed, facets if facets else None)

    if not post_result:
        print("   FAILED to create post")
        sys.exit(1)

    post_uri = post_result['uri']
    post_cid = post_result['cid']
    print(f"   Created: {post_uri}")

    # Extract rkey from post URI for Bluesky URL
    post_rkey = post_uri.split('/')[-1]
    bsky_url = f"https://bsky.app/profile/{handle}/post/{post_rkey}"
    print(f"   Bluesky URL: {bsky_url}")

    # Update document with bskyPostRef
    if not args.no_update:
        print(f"\n5. Updating document with bskyPostRef...")
        record['bskyPostRef'] = {
            "uri": post_uri,
            "cid": post_cid
        }
        update_result = put_record(pds, session, collection, args.rkey, record)
        if update_result:
            print(f"   Document updated!")
        else:
            print(f"   WARNING: Failed to update document")

    # Summary
    print("\n" + "=" * 60)
    print("Done!")
    print(f"\nBluesky post: {bsky_url}")
    print(f"Document: {doc_url}")
    print("=" * 60)

if __name__ == "__main__":
    main()
