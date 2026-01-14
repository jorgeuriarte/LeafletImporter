# Tumblr to Leaflet Migrator

Migrate your Tumblr blog to [Leaflet.pub](https://leaflet.pub).

## Why migrate to Leaflet.pub?

Tumblr was great, but it's been in decline for years. Ownership changes, uncertain future, less and less development. Your content is trapped in a platform you don't control.

Leaflet is different: it's built on [AT Protocol](https://atproto.com/) (the protocol behind Bluesky). Your posts live on your personal PDS—the same place your Bluesky data lives. If Leaflet disappears tomorrow, your data is still yours.

Also:
- **No lock-in.** Your posts are portable.
- **Actually social.** Readers follow you via Bluesky or RSS.
- **Custom domain** if you want.
- **Comments and discovery** through Bluesky's social graph.

More at [about.leaflet.pub](https://about.leaflet.pub/).

## The Tool

A browser-based migrator. No installs, no backend—your credentials never leave your browser.

**Live:** [leafletimporter.pages.dev](https://leafletimporter.pages.dev/)

### How it works

1. Enter your Tumblr blog URL (e.g., `myblog.tumblr.com`)
2. Connect your Bluesky account with an [App Password](https://bsky.app/settings/app-passwords)
3. Select your Leaflet publication
4. Migrate

The tool fetches your Tumblr RSS, converts posts to Leaflet format (preserving formatting, images, links), and publishes via AT Protocol.

### Features

- **Batch migration** with progress tracking
- **Preserves formatting:** headers, code blocks, lists, quotes, links
- **Uploads images** as blobs (embedded, not external links)
- **Pause/resume** if you need to step away

### Safe to re-import

Each Tumblr post generates a unique identifier based on its URL. Running the migration again updates existing posts instead of duplicating them. Useful if you add new posts to Tumblr or want to fix something.

## Architecture

```
┌──────────────────────────────────────┐
│  Your Browser                        │
│  leafletimporter.pages.dev           │
└──────────────┬───────────────────────┘
               │
    ┌──────────┴──────────┐
    │                     │
    ▼                     ▼
┌─────────────┐    ┌─────────────────┐
│ CORS Proxy  │    │ Bluesky/AT API  │
│ (Worker)    │    │ bsky.social     │
└──────┬──────┘    └─────────────────┘
       │                    ▲
       ▼                    │
┌─────────────┐      Credentials stay
│ Tumblr RSS  │      in your browser
│ & Images    │
└─────────────┘
```

The CORS proxy (a Cloudflare Worker) is needed because browsers block direct requests to Tumblr. It just passes data through—no credentials, no storage.

## Local development

```bash
# Start local server + worker proxy
./scripts/deploy.sh dev

# Open http://localhost:8080
```

The app auto-detects local vs production and uses the appropriate proxy URL.

## Python CLI (alternative)

For more control or batch processing from exports:

```bash
source venv/bin/activate
python import_tumblr.py tumblr-export.zip  # Parse Tumblr export
python publish_to_leaflet.py               # Publish to Leaflet
```

## License

MIT
