# LeafletImporter - Project Instructions

## Overview

This project provides tools to migrate blogs from Tumblr to Leaflet.pub via AT Protocol.

**Components:**
1. **Python CLI tools** - For batch import from Tumblr exports
2. **Web App** - Browser-based importer (client-side, no server needed)
3. **Cloudflare Worker** - CORS proxy for Tumblr RSS feeds

---

## Quick Commands

```bash
# Start local web server
./scripts/deploy.sh local

# Stop local web server
./scripts/deploy.sh stop

# Check status
./scripts/deploy.sh status

# Deploy Cloudflare Worker
./scripts/deploy.sh worker

# Run worker locally for testing
./scripts/deploy.sh worker-dev
```

---

## Local Development Environment

### Web App

```bash
# Start server
./scripts/deploy.sh local
# Opens at: http://localhost:8080

# Stop server
./scripts/deploy.sh stop
```

**Files:**
- `web/index.html` - Main app (HTML + CSS + JS in one file)
- `web/worker.js` - Cloudflare Worker source
- `web/wrangler.toml` - Worker deployment config

**Configuration (in index.html):**
```javascript
const TUMBLR_PROXY_URL = 'https://tumblr-proxy.workers.dev';
const USE_MOCK_DATA = true;  // Set to false when proxy is deployed
```

### Python CLI Tools

```bash
# Activate virtual environment
source venv/bin/activate

# Import from Tumblr export
python import_tumblr.py tumblr-export.zip

# Publish to Leaflet
python publish_to_leaflet.py

# Add Wayback links to broken URLs
python add_wayback_links.py
```

**Credentials:**
- Store in `.credentials` file (gitignored)
- Format:
  ```
  HANDLE=yourhandle.bsky.social
  APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
  ```

---

## Cloudflare Worker (Tumblr Proxy)

The worker proxies Tumblr RSS requests to bypass CORS restrictions.

### Deploy

```bash
# First time: login to Cloudflare
npx wrangler login

# Deploy
./scripts/deploy.sh worker
# or
cd web && npx wrangler deploy
```

### Test Locally

```bash
./scripts/deploy.sh worker-dev
# Worker runs at: http://localhost:8787
```

### Usage

```
GET https://tumblr-proxy.<subdomain>.workers.dev?url=https://myblog.tumblr.com/rss
```

---

## Production Environment

### GitHub Pages (Static Site)

```bash
# Push to trigger deployment
./scripts/deploy.sh pages

# Or manually:
git push origin main
```

**Setup:**
1. Go to repo Settings > Pages
2. Source: Deploy from branch
3. Branch: main, folder: /web
4. URL: `https://<user>.github.io/LeafletImporter/`

### Cloudflare Worker (Production)

**Current URL:** `https://tumblr-proxy.<TBD>.workers.dev`

**To update:**
1. Edit `web/worker.js`
2. Run `./scripts/deploy.sh worker`

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              GitHub Pages (Static)                   │
│                   web/index.html                     │
└─────────────────────┬───────────────────────────────┘
                      │
         ┌────────────┴────────────┐
         │                         │
         ▼                         ▼
┌─────────────────┐      ┌─────────────────┐
│ CF Worker       │      │ AT-Proto APIs   │
│ (Tumblr proxy)  │      │ (bsky.social)   │
│                 │      │                 │
│ Adds CORS       │      │ Direct from     │
│ headers         │      │ browser         │
└────────┬────────┘      └─────────────────┘
         │                        ▲
         ▼                        │
┌─────────────────┐               │
│ Tumblr RSS      │      🔒 Credentials never
│ *.tumblr.com    │         leave the browser
└─────────────────┘
```

---

## Key Files

| File | Purpose |
|------|---------|
| `web/index.html` | Web app (single file) |
| `web/worker.js` | Cloudflare Worker |
| `scripts/deploy.sh` | Deployment helper |
| `publish_to_leaflet.py` | CLI publisher |
| `import_tumblr.py` | Tumblr export parser |
| `add_wayback_links.py` | Broken link fixer |
| `output/posts/` | Converted posts |
| `.credentials` | Bluesky credentials (gitignored) |

---

## Troubleshooting

### "CORS error" in browser console
- Make sure Cloudflare Worker is deployed
- Update `TUMBLR_PROXY_URL` in index.html
- Set `USE_MOCK_DATA = false`

### "Authentication failed" on Bluesky
- Check handle format (include .bsky.social if needed)
- Generate new App Password at bsky.app/settings/app-passwords
- App Passwords format: `xxxx-xxxx-xxxx-xxxx`

### Local server won't start
- Check if port 8080 is in use: `lsof -i:8080`
- Kill existing process: `./scripts/deploy.sh stop`
- Or use different port in deploy.sh

### Worker deployment fails
- Run `npx wrangler login` first
- Check wrangler.toml configuration

---

## TODO

- [ ] Deploy Cloudflare Worker to production
- [ ] Configure custom domain for worker (optional)
- [ ] Set up GitHub Pages
- [ ] Add incremental import feature to web app
- [ ] Add progress persistence (localStorage)
