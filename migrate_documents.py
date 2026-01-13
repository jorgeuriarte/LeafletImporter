#!/usr/bin/env python3
"""
Migrate Leaflet documents from hex rkey format to proper TID format.

This fixes the issue where documents imported with hex rkeys don't show
delete/edit options in the Leaflet UI.
"""

import json
import time
import uuid
import requests
from pathlib import Path

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


def is_hex_rkey(rkey: str) -> bool:
    """Check if rkey is in hex format (needs migration)."""
    # Hex rkeys contain only 0-9 and a-f
    # TID rkeys use base32 charset which includes letters outside hex range (g-z)
    #
    # Examples:
    #   Hex: 6484ca9dd98c0 (only hex chars)
    #   TID: 3mcdaxomze22m (contains m, o, x, z which are not hex)

    hex_chars = set("0123456789abcdef")
    rkey_chars = set(rkey.lower())

    # If all characters are valid hex chars, it's a hex rkey
    return rkey_chars.issubset(hex_chars)


def load_credentials(filepath: str = ".credentials") -> tuple[str, str]:
    """Load credentials from file."""
    creds = {}
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, value = line.split("=", 1)
                creds[key.strip()] = value.strip()
    return creds["HANDLE"], creds["APP_PASSWORD"]


def create_session(handle: str, password: str) -> dict:
    """Create an authenticated session."""
    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
    )
    resp.raise_for_status()
    return resp.json()


def get_record(session: dict, rkey: str) -> dict | None:
    """Get a document record by rkey."""
    try:
        resp = requests.get(
            "https://bsky.social/xrpc/com.atproto.repo.getRecord",
            params={
                "repo": session["did"],
                "collection": "pub.leaflet.document",
                "rkey": rkey,
            },
            headers={"Authorization": f"Bearer {session['accessJwt']}"},
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"Error getting record: {e}")
    return None


def create_record(session: dict, rkey: str, record: dict) -> dict:
    """Create a new document record."""
    resp = requests.post(
        "https://bsky.social/xrpc/com.atproto.repo.putRecord",
        headers={
            "Authorization": f"Bearer {session['accessJwt']}",
            "Content-Type": "application/json",
        },
        json={
            "repo": session["did"],
            "collection": "pub.leaflet.document",
            "rkey": rkey,
            "record": record,
            "validate": False,
        },
    )
    resp.raise_for_status()
    return resp.json()


def delete_record(session: dict, rkey: str) -> bool:
    """Delete a document record."""
    try:
        resp = requests.post(
            "https://bsky.social/xrpc/com.atproto.repo.deleteRecord",
            headers={
                "Authorization": f"Bearer {session['accessJwt']}",
                "Content-Type": "application/json",
            },
            json={
                "repo": session["did"],
                "collection": "pub.leaflet.document",
                "rkey": rkey,
            },
        )
        return resp.ok
    except Exception:
        return False


def fix_record_format(record: dict) -> dict:
    """Fix record format to match Leaflet native format."""
    # Add tags if missing
    if "tags" not in record:
        record["tags"] = []

    # Fix page IDs to use UUIDs
    for page in record.get("pages", []):
        if "id" in page and not "-" in page["id"]:
            # Replace simple ID with UUID
            page["id"] = str(uuid.uuid4())

    return record


def migrate_documents(dry_run: bool = False, limit: int | None = None):
    """Migrate all documents with hex rkeys to TID format."""
    print("=" * 60)
    print("Leaflet Document Migration")
    print("=" * 60)

    # Load credentials and create session
    handle, password = load_credentials()
    session = create_session(handle, password)
    did = session["did"]
    print(f"Authenticated as: {did}")

    # Load published.json for tracking
    published_file = Path("output/posts/published.json")
    published = {}
    if published_file.exists():
        with open(published_file) as f:
            published = json.load(f)

    # List all documents
    print("\nFetching all documents...")
    all_records = []
    cursor = None

    while True:
        params = {
            "repo": did,
            "collection": "pub.leaflet.document",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            "https://bsky.social/xrpc/com.atproto.repo.listRecords",
            params=params,
            headers={"Authorization": f"Bearer {session['accessJwt']}"},
        )

        data = resp.json()
        all_records.extend(data.get("records", []))
        cursor = data.get("cursor")
        if not cursor:
            break

    print(f"Found {len(all_records)} documents")

    # Find documents needing migration
    to_migrate = []
    for rec in all_records:
        uri = rec.get("uri", "")
        rkey = uri.split("/")[-1]
        if is_hex_rkey(rkey):
            to_migrate.append({
                "uri": uri,
                "rkey": rkey,
                "value": rec.get("value", {}),
                "title": rec.get("value", {}).get("title", "(no title)"),
            })

    print(f"Documents needing migration: {len(to_migrate)}")

    if limit:
        to_migrate = to_migrate[:limit]
        print(f"Limiting to {limit} documents")

    if dry_run:
        print("\n[DRY RUN - No changes will be made]")
        for doc in to_migrate[:10]:
            print(f"  Would migrate: {doc['title'][:50]}")
            print(f"    Old rkey: {doc['rkey']}")
            print(f"    New rkey: {generate_tid()}")
        if len(to_migrate) > 10:
            print(f"  ... and {len(to_migrate) - 10} more")
        return

    # Perform migration
    print("\nStarting migration...")
    migrated = 0
    errors = 0

    # Create mapping for published.json updates
    rkey_mapping = {}

    for i, doc in enumerate(to_migrate, 1):
        old_rkey = doc["rkey"]
        title = doc["title"][:40]
        print(f"  [{i}/{len(to_migrate)}] {title}...")

        try:
            # Generate new TID rkey
            new_rkey = generate_tid()

            # Fix record format
            new_record = fix_record_format(doc["value"].copy())

            # Create new record
            result = create_record(session, new_rkey, new_record)
            new_uri = result["uri"]

            # Delete old record
            if delete_record(session, old_rkey):
                print(f"    Migrated: {old_rkey} -> {new_rkey}")
                rkey_mapping[old_rkey] = {
                    "new_rkey": new_rkey,
                    "new_uri": new_uri,
                    "new_cid": result["cid"],
                }
                migrated += 1
            else:
                print(f"    Warning: Created new but failed to delete old")
                migrated += 1

            # Rate limiting
            time.sleep(0.3)

        except Exception as e:
            print(f"    ERROR: {e}")
            errors += 1
            continue

    # Update published.json with new URIs
    print("\nUpdating published.json...")
    updated_entries = 0
    for post_id, info in published.items():
        old_uri = info.get("uri", "")
        old_rkey = old_uri.split("/")[-1] if old_uri else ""

        if old_rkey in rkey_mapping:
            mapping = rkey_mapping[old_rkey]
            info["uri"] = mapping["new_uri"]
            info["cid"] = mapping["new_cid"]
            info["old_uri"] = old_uri  # Keep for reference
            updated_entries += 1

    if updated_entries > 0:
        with open(published_file, "w") as f:
            json.dump(published, f, indent=2)
        print(f"Updated {updated_entries} entries in published.json")

    print("\n" + "=" * 60)
    print(f"Migration complete!")
    print(f"  Migrated: {migrated}")
    print(f"  Errors: {errors}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate Leaflet documents to TID format")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--limit", type=int, help="Limit number of documents to migrate")

    args = parser.parse_args()
    migrate_documents(dry_run=args.dry_run, limit=args.limit)
