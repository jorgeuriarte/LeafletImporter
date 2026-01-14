# Tumblr to Leaflet Migrator

Migrate your Tumblr blog to [Leaflet.pub](https://leaflet.pub).

## Why Leaflet?

Leaflet is a publishing platform built on [AT Protocol](https://atproto.com/) (the protocol behind Bluesky). Your posts live on your PDS—the same place your Bluesky data lives—open and under your control.

Key points:
- **Your data, your control.** No platform lock-in.
- **Social by default.** Readers follow via Bluesky feeds or RSS.
- **Custom domains.** Use your own domain if you want.
- **Comments and discovery** through the Bluesky social graph.

More at [about.leaflet.pub](https://about.leaflet.pub/).

## The Tool

A browser-based migrator. No installs, no servers, your credentials never leave your browser.

**Live:** [leafletimporter.pages.dev](https://leafletimporter.pages.dev/)

### How it works

1. Enter your Tumblr blog URL (e.g., `myblog.tumblr.com`)
2. Connect your Bluesky account with an [App Password](https://bsky.app/settings/app-passwords)
3. Select your Leaflet publication
4. Migrate

The tool fetches your Tumblr RSS, converts posts to Leaflet format (preserving formatting, images, links), and publishes them via AT Protocol.

### Features

- **Batch migration** with progress tracking
- **Preserves formatting:** headers, code blocks, lists, quotes, links
- **Uploads images** as blobs (not just links)
- **Pause/resume** if you need to step away

### Safe to re-import

Each Tumblr post generates a unique identifier based on its URL. If you run the migration again, existing posts get updated instead of duplicated. Useful if you add new posts to your Tumblr or want to fix something.

### Local development

```bash
# Start local server + worker proxy
./scripts/deploy.sh dev

# Open http://localhost:8080
```

Requires the Cloudflare Worker for CORS proxying of Tumblr feeds. See `web/worker.js`.

## Also included

Python CLI tools for more control:

```bash
source venv/bin/activate
python import_tumblr.py tumblr-export.zip  # Parse Tumblr export
python publish_to_leaflet.py               # Publish to Leaflet
```

## License

MIT
