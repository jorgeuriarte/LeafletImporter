#!/usr/bin/env python3
"""
Copy a Leaflet document to a different publication.

The original document is preserved. Use delete_document.py to remove it
after verifying the copy was successful.

Usage:
    python copy_document.py --rkey 3mdvzhtuybc2z --to-pub 3mcd7ta3x3c26
    python copy_document.py --rkey 3mdvzhtuybc2z --to-pub 3mcd7ta3x3c26 --dry-run
    python copy_document.py --rkey 3mdvzhtuybc2z --to-pub 3mcd7ta3x3c26 --target-rkey xyz789
"""

import argparse
import requests
import sys
import time
import random

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
        print(f"Error: {path} not found. Create it with HANDLE and APP_PASSWORD.")
        sys.exit(1)

    if 'HANDLE' not in creds or 'APP_PASSWORD' not in creds:
        print("Error: .credentials must contain HANDLE and APP_PASSWORD")
        sys.exit(1)

    return creds

def resolve_pds(handle):
    """Resolve handle to DID and PDS endpoint"""
    # Resolve handle to DID
    response = requests.get(
        f"https://bsky.social/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": handle}
    )
    if response.status_code != 200:
        print(f"Failed to resolve handle: {response.text}")
        sys.exit(1)

    did = response.json()['did']

    # Get PDS from DID document
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
    """
    Generate a valid AT Protocol TID (Timestamp ID).
    TID format: 13 characters, base32-sortable encoding of microseconds since epoch.
    """
    # Base32 alphabet used by AT Protocol (lowercase)
    B32_CHARSET = "234567abcdefghijklmnopqrstuvwxyz"

    # Microseconds since Unix epoch
    timestamp_us = int(time.time() * 1_000_000)

    # Add some randomness to the lower bits to avoid collisions
    clock_id = random.randint(0, 1023)
    tid_int = (timestamp_us << 10) | clock_id

    # Encode as base32
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
        print(f"Failed to create record: {response.status_code}")
        print(response.text)
        return None

    return response.json()

def delete_record(pds, session, collection, rkey):
    """Delete a record"""
    response = requests.post(
        f"{pds}/xrpc/com.atproto.repo.deleteRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={
            "repo": session['did'],
            "collection": collection,
            "rkey": rkey
        }
    )

    return response.status_code == 200

# =============================================================================
# Document Detection
# =============================================================================

def detect_document_format(pds, did, rkey):
    """Detect if document is in old or new format"""
    # Try new format first
    record = get_record(pds, did, "site.standard.document", rkey)
    if record:
        return "site.standard.document", record

    # Try old format
    record = get_record(pds, did, "pub.leaflet.document", rkey)
    if record:
        return "pub.leaflet.document", record

    return None, None

def get_publication_field(record, collection):
    """Get publication reference from record based on format"""
    if collection == "site.standard.document":
        return record.get('site', '')
    else:
        return record.get('publication', '')

def set_publication_field(record, collection, new_pub_uri):
    """Set publication reference in record based on format"""
    if collection == "site.standard.document":
        record['site'] = new_pub_uri
    else:
        record['publication'] = new_pub_uri

# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Copy a Leaflet document to a different publication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --rkey 3mdvzhtuybc2z --to-pub 3mcd7ta3x3c26
  %(prog)s --rkey 3mdvzhtuybc2z --to-pub 3mcd7ta3x3c26 --dry-run
  %(prog)s --rkey 3mdvzhtuybc2z --to-pub 3mcd7ta3x3c26 --target-rkey abc123  # Update existing copy

After copying, use delete_document.py to remove the original if needed.
        """
    )
    parser.add_argument('--rkey', required=True, help='Document rkey to copy')
    parser.add_argument('--to-pub', required=True, help='Target publication rkey')
    parser.add_argument('--target-rkey',
                        help='Specific rkey for the copy (overwrites if exists). Use to update a previous copy.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--credentials', default='.credentials',
                        help='Path to credentials file (default: .credentials)')

    args = parser.parse_args()

    print("=" * 60)
    print("Leaflet Document Copier")
    print("=" * 60)

    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]")

    # Load credentials and authenticate
    creds = load_credentials(args.credentials)
    handle = creds['HANDLE']

    print(f"\n1. Authenticating as {handle}...")
    did, pds = resolve_pds(handle)
    print(f"   DID: {did}")
    print(f"   PDS: {pds}")

    session = create_session(pds, handle, creds['APP_PASSWORD'])
    print(f"   Authenticated!")

    # Find the document
    print(f"\n2. Looking for document {args.rkey}...")
    collection, doc_response = detect_document_format(pds, did, args.rkey)

    if not collection:
        print(f"   ERROR: Document not found in either format")
        sys.exit(1)

    record = doc_response['value']
    print(f"   Found in collection: {collection}")
    print(f"   Title: {record.get('title', 'Untitled')}")

    current_pub = get_publication_field(record, collection)
    print(f"   Current publication: {current_pub}")

    # Build new publication URI
    if collection == "site.standard.document":
        new_pub_uri = f"at://{did}/site.standard.publication/{args.to_pub}"
    else:
        new_pub_uri = f"at://{did}/pub.leaflet.publication/{args.to_pub}"

    print(f"\n3. Copying to publication: {new_pub_uri}")

    # Prepare new record (copy, not modify original)
    new_record = record.copy()
    set_publication_field(new_record, collection, new_pub_uri)

    # Determine target rkey
    if args.target_rkey:
        new_rkey = args.target_rkey
        print(f"   Target rkey: {new_rkey} (will overwrite if exists)")
    else:
        new_rkey = generate_tid()
        print(f"   New rkey: {new_rkey}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would create document at:")
        print(f"   at://{did}/{collection}/{new_rkey}")
        print(f"\n[DRY RUN] Original document will be preserved at:")
        print(f"   at://{did}/{collection}/{args.rkey}")
        print("\nNo changes made.")
        return

    # Create new document
    print(f"\n4. Creating document...")
    result = put_record(pds, session, collection, new_rkey, new_record)

    if not result:
        print("   FAILED to create document")
        sys.exit(1)

    print(f"   Created: {result['uri']}")
    print(f"   CID: {result['cid']}")

    # Summary
    print("\n" + "=" * 60)
    print("Done! Document copied successfully.")
    print(f"\nNew document:      https://leaflet.pub/lish/{did}/{args.to_pub}/{new_rkey}")
    print(f"Original document: https://leaflet.pub/lish/{did}/{current_pub.split('/')[-1]}/{args.rkey}")
    print(f"\nTo delete the original, run:")
    print(f"  python delete_document.py --rkey {args.rkey}")
    print("=" * 60)

if __name__ == "__main__":
    main()
