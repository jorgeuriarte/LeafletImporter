# Tumblr to Leaflet Migrator

Migrate your Tumblr blog to [Leaflet.pub](https://leaflet.pub) — take your writing to the open web.

## Why Leaflet?

Your words deserve a home you actually own.

[Leaflet](https://leaflet.pub) is built on [AT Protocol](https://atproto.com/), the open protocol behind Bluesky. This means:

- **Your data is yours.** Posts live in your personal data repository. You can back them up, move them, or take them elsewhere—no permission needed.
- **No lock-in.** AT Protocol is designed for portability. If you want to leave, you leave with everything.
- **Open by design.** Your content is accessible via RSS, Bluesky's social graph, or any AT Protocol client. No walled gardens.
- **Interconnected.** Readers discover and follow you through Bluesky. Comments flow naturally from the social layer.

This isn't just another blogging platform. It's publishing on infrastructure that respects your freedom.

More at [about.leaflet.pub](https://about.leaflet.pub/).

## The Tool

A browser-based migrator. No installs, no backend—your credentials never leave your browser.

**Live:** [leafletimporter.pages.dev](https://leafletimporter.pages.dev/)

### How it works

1. Enter your Tumblr blog URL (e.g., `myblog.tumblr.com`)
2. Connect your Bluesky account with an [App Password](https://bsky.app/settings/app-passwords)
3. Select your Leaflet publication
4. Migrate

The tool fetches your Tumblr posts via API, converts them to Leaflet format (preserving formatting, images, links), and publishes via AT Protocol.

### Features

- **Full archive migration** — fetches all posts, not just recent ones
- **Preserves formatting:** headers, code blocks, lists, quotes, links
- **Uploads images** as blobs (embedded, not external links)
- **Progress tracking** with pause/resume
- **Idempotent imports** — safe to re-run without duplicates

### Safe to re-run

Each Tumblr post generates a unique identifier based on its content hash. Running the migration again updates existing posts instead of duplicating them. Add new posts to Tumblr? Just run it again.

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
│ Tumblr API  │      in your browser
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

See [CLAUDE.md](CLAUDE.md) for full development and deployment documentation.

## License

MIT
