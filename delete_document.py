#!/usr/bin/env python3
"""
Delete Leaflet documents from your PDS.

This is useful for documents created via API that can't be deleted from
Leaflet's web UI (because they lack the Supabase index reference).

Usage:
    python delete_document.py --rkey 3mdvzhtuybc2z
    python delete_document.py --rkey 3mdvzhtuybc2z --dry-run
    python delete_document.py --list                          # List all documents
    python delete_document.py --list --publication 3mcd7ta3x3c26  # List docs in publication
"""

import argparse
import requests
import sys

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

def list_records(pds, did, collection, limit=100):
    """List all records in a collection"""
    records = []
    cursor = None

    while True:
        params = {
            "repo": did,
            "collection": collection,
            "limit": min(limit, 100)
        }
        if cursor:
            params["cursor"] = cursor

        response = requests.get(f"{pds}/xrpc/com.atproto.repo.listRecords", params=params)

        if response.status_code != 200:
            break

        data = response.json()
        records.extend(data.get('records', []))

        cursor = data.get('cursor')
        if not cursor or len(records) >= limit:
            break

    return records

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
    record = get_record(pds, did, "site.standard.document", rkey)
    if record:
        return "site.standard.document", record

    record = get_record(pds, did, "pub.leaflet.document", rkey)
    if record:
        return "pub.leaflet.document", record

    return None, None

def get_publication_rkey(record, collection):
    """Extract publication rkey from record"""
    if collection == "site.standard.document":
        site = record.get('site', '')
    else:
        site = record.get('publication', '')

    # Extract rkey from URI like at://did:plc:xxx/collection/rkey
    if site and '/' in site:
        return site.split('/')[-1]
    return None

# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Delete Leaflet documents from your PDS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --rkey 3mdvzhtuybc2z              # Delete specific document
  %(prog)s --rkey 3mdvzhtuybc2z --dry-run    # Show what would be deleted
  %(prog)s --list                            # List all your documents
  %(prog)s --list --publication 3mcd7ta3x3c26  # List docs in specific publication
        """
    )
    parser.add_argument('--rkey', help='Document rkey to delete')
    parser.add_argument('--list', action='store_true', help='List documents instead of deleting')
    parser.add_argument('--publication', help='Filter by publication rkey (with --list)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--force', action='store_true',
                        help='Skip confirmation prompt')
    parser.add_argument('--credentials', default='.credentials',
                        help='Path to credentials file (default: .credentials)')

    args = parser.parse_args()

    if not args.rkey and not args.list:
        parser.error("Either --rkey or --list is required")

    # Load credentials and authenticate
    creds = load_credentials(args.credentials)
    handle = creds['HANDLE']

    print(f"Authenticating as {handle}...")
    did, pds = resolve_pds(handle)

    if args.list:
        # List mode - no auth needed for listing own records
        print(f"\nListing documents for {did}...\n")

        # Get documents from both collections
        docs = []

        for collection in ["site.standard.document", "pub.leaflet.document"]:
            records = list_records(pds, did, collection)
            for record in records:
                rkey = record['uri'].split('/')[-1]
                value = record['value']
                pub_rkey = get_publication_rkey(value, collection)

                # Filter by publication if specified
                if args.publication and pub_rkey != args.publication:
                    continue

                docs.append({
                    'rkey': rkey,
                    'title': value.get('title', 'Untitled')[:50],
                    'collection': collection.split('.')[-1],  # "document"
                    'format': 'new' if 'site.standard' in collection else 'old',
                    'publication': pub_rkey or '(none)',
                    'publishedAt': value.get('publishedAt', '')[:10],
                })

        if not docs:
            print("No documents found.")
            return

        # Print table
        print(f"{'RKEY':<20} {'TITLE':<40} {'PUB':<15} {'DATE':<12} {'FMT'}")
        print("-" * 100)
        for doc in docs:
            print(f"{doc['rkey']:<20} {doc['title']:<40} {doc['publication']:<15} {doc['publishedAt']:<12} {doc['format']}")

        print(f"\nTotal: {len(docs)} documents")
        return

    # Delete mode
    session = create_session(pds, handle, creds['APP_PASSWORD'])

    print(f"\nLooking for document {args.rkey}...")
    collection, doc_response = detect_document_format(pds, did, args.rkey)

    if not collection:
        print(f"ERROR: Document not found")
        sys.exit(1)

    record = doc_response['value']
    title = record.get('title', 'Untitled')
    pub = get_publication_rkey(record, collection)

    print(f"\nDocument found:")
    print(f"  Title:       {title}")
    print(f"  Collection:  {collection}")
    print(f"  Publication: {pub}")
    print(f"  URI:         at://{did}/{collection}/{args.rkey}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would delete this document. No changes made.")
        return

    # Confirm deletion
    if not args.force:
        confirm = input(f"\nDelete this document? (yes/no): ").strip().lower()
        if confirm != 'yes':
            print("Cancelled.")
            return

    # Delete
    print(f"\nDeleting...")
    if delete_record(pds, session, collection, args.rkey):
        print(f"Deleted successfully!")
    else:
        print(f"ERROR: Failed to delete")
        sys.exit(1)

if __name__ == "__main__":
    main()
