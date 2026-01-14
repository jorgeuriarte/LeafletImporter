# LeafletImporter - Project Instructions

## Overview

This project provides tools to migrate blogs from Tumblr to Leaflet.pub via AT Protocol.

**Components:**
1. **Web App** - Browser-based importer at https://leafletimporter.pages.dev
2. **Cloudflare Worker** - CORS proxy at https://tumblr-proxy.jorge-uriarte.workers.dev
3. **Python CLI tools** - For batch import from Tumblr exports

---

## Production URLs

| Component | URL |
|-----------|-----|
| Web App | https://leafletimporter.pages.dev |
| Alt Domain | https://leafletmigrator.omelas.net |
| CORS Proxy | https://tumblr-proxy.jorge-uriarte.workers.dev |
| Version Check | https://tumblr-proxy.jorge-uriarte.workers.dev/version |

---

## Development Cycle

### 1. Local Development

```bash
# Start full local environment (web + worker)
./scripts/deploy.sh dev

# URLs:
# - Web App:  http://localhost:8080
# - Worker:   http://localhost:8787

# Check status
./scripts/deploy.sh status

# Stop all
./scripts/deploy.sh stop
```

### 2. Deploy to Production

```bash
# Deploy web app to Cloudflare Pages (with version)
./scripts/deploy.sh web

# Deploy worker to Cloudflare (with version)
./scripts/deploy.sh worker
```

**Both commands automatically:**
- Inject git hash + timestamp into the code
- Deploy to Cloudflare
- Restore source files (keep git clean)

### 3. Version Format

Versions are displayed in the footer of the web app:
```
Web: abc1234 (2026-01-14 12:00 UTC) | Proxy: def5678 (2026-01-14 11:00 UTC)
```

---

## Quick Commands Reference

```bash
# === Local Development ===
./scripts/deploy.sh dev         # Start web server + worker
./scripts/deploy.sh stop        # Stop all local services
./scripts/deploy.sh status      # Check what's running
./scripts/deploy.sh local       # Web server only (port 8080)
./scripts/deploy.sh worker-dev  # Worker only (port 8787)

# === Production Deployment ===
./scripts/deploy.sh web         # Build + deploy web to CF Pages
./scripts/deploy.sh worker      # Deploy worker to CF Workers

# === Other ===
./scripts/deploy.sh build       # Build web locally (test only)
./scripts/deploy.sh pages       # Push to GitHub
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│         Cloudflare Pages (Static)                   │
│         leafletimporter.pages.dev                   │
│              web/index.html                         │
└─────────────────────┬───────────────────────────────┘
                      │
         ┌────────────┴────────────┐
         │                         │
         ▼                         ▼
┌─────────────────┐      ┌─────────────────┐
│ CF Worker       │      │ AT-Proto APIs   │
│ tumblr-proxy    │      │ (bsky.social)   │
│                 │      │                 │
│ Adds CORS       │      │ Direct from     │
│ headers         │      │ browser         │
└────────┬────────┘      └─────────────────┘
         │                        ▲
         ▼                        │
┌─────────────────┐               │
│ Tumblr API v1   │      🔒 Credentials never
│ *.tumblr.com    │         leave the browser
└─────────────────┘
```

---

## Key Files

| File | Purpose |
|------|---------|
| `web/index.html` | Web app (single file, all HTML+CSS+JS) |
| `web/worker.js` | Cloudflare Worker (CORS proxy) |
| `scripts/deploy.sh` | All deployment commands |
| `package.json` | npm build script for CF Pages |
| `.credentials` | Bluesky credentials (gitignored) |

### Python CLI (optional)

| File | Purpose |
|------|---------|
| `import_tumblr.py` | Parse Tumblr export ZIP |
| `publish_to_leaflet.py` | Batch publish to Leaflet |
| `add_wayback_links.py` | Fix broken URLs with Wayback |

---

## Configuration

The web app auto-detects environment:

```javascript
// Automatic - no manual config needed
const isLocalDev = window.location.hostname === 'localhost';
const TUMBLR_PROXY_URL = isLocalDev
  ? 'http://localhost:8787'
  : 'https://tumblr-proxy.jorge-uriarte.workers.dev';
```

### Credentials

Store in `.credentials` file (gitignored):
```
HANDLE=yourhandle.bsky.social
APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

---

## Troubleshooting

### "CORS error" in browser
- Local: Run `./scripts/deploy.sh dev` to start worker
- Production: Check worker is deployed with `curl https://tumblr-proxy.jorge-uriarte.workers.dev/version`

### "Authentication failed" on Bluesky
- Check handle format (include .bsky.social)
- Generate new App Password at bsky.app/settings/app-passwords

### Version shows "dev" in production
- Run `./scripts/deploy.sh web` to deploy with version injection
- Check CF Pages is serving from the latest deployment

### Worker deployment fails
- Run `npx wrangler login` first
- Check `web/wrangler.toml` configuration

---

## TODO

- [x] Deploy Cloudflare Worker to production
- [x] Deploy web app to Cloudflare Pages
- [x] Add automatic version injection
- [ ] Add incremental import feature
- [ ] Add progress persistence (localStorage)
- [ ] Handle rate limiting gracefully
