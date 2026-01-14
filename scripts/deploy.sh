#!/bin/bash
#
# Deploy script for Tumblr to Leaflet web app
# Usage: ./scripts/deploy.sh [command]
#
# Commands:
#   local       - Start local development server
#   stop        - Stop local development server
#   worker      - Deploy Cloudflare Worker
#   worker-dev  - Run Cloudflare Worker locally
#   pages       - Deploy to GitHub Pages (push to main)
#   status      - Show status of all services
#

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$PROJECT_ROOT/web"
PID_FILE="$PROJECT_ROOT/.local-server.pid"
LOCAL_PORT=8080

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================
# Local Development Server
# ============================================
start_local() {
    log_info "Starting local development server..."

    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Server already running on PID $pid"
            log_info "URL: http://localhost:$LOCAL_PORT"
            return 0
        fi
    fi

    cd "$WEB_DIR"

    # Try Python 3 first, then Python 2
    if command -v python3 &>/dev/null; then
        python3 -m http.server $LOCAL_PORT &
    elif command -v python &>/dev/null; then
        python -m SimpleHTTPServer $LOCAL_PORT &
    else
        log_error "Python not found. Install Python or use another HTTP server."
        exit 1
    fi

    local pid=$!
    echo $pid > "$PID_FILE"

    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log_success "Server started on PID $pid"
        log_info "URL: http://localhost:$LOCAL_PORT"
        log_info "To stop: ./scripts/deploy.sh stop"
    else
        log_error "Failed to start server"
        rm -f "$PID_FILE"
        exit 1
    fi
}

stop_local() {
    log_info "Stopping local development server..."

    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            rm -f "$PID_FILE"
            log_success "Server stopped (PID $pid)"
        else
            log_warn "Server not running (stale PID file)"
            rm -f "$PID_FILE"
        fi
    else
        log_warn "No PID file found. Server may not be running."
        # Try to find and kill any python http server on our port
        local pids=$(lsof -ti:$LOCAL_PORT 2>/dev/null || true)
        if [ -n "$pids" ]; then
            log_info "Found process on port $LOCAL_PORT, killing..."
            echo "$pids" | xargs kill 2>/dev/null || true
            log_success "Killed processes on port $LOCAL_PORT"
        fi
    fi
}

# ============================================
# Cloudflare Worker
# ============================================
deploy_worker() {
    log_info "Deploying Cloudflare Worker..."

    if ! command -v wrangler &>/dev/null && ! command -v npx &>/dev/null; then
        log_error "wrangler not found. Install with: npm install -g wrangler"
        exit 1
    fi

    cd "$WEB_DIR"

    if command -v wrangler &>/dev/null; then
        wrangler deploy
    else
        npx wrangler deploy
    fi

    log_success "Worker deployed!"
    log_info "URL: https://tumblr-proxy.<your-subdomain>.workers.dev"
}

run_worker_dev() {
    log_info "Starting Cloudflare Worker in dev mode..."

    cd "$WEB_DIR"

    if command -v wrangler &>/dev/null; then
        wrangler dev
    else
        npx wrangler dev
    fi
}

# ============================================
# GitHub Pages
# ============================================
deploy_pages() {
    log_info "Deploying to GitHub Pages..."

    cd "$PROJECT_ROOT"

    # Check if we're on the right branch
    local branch=$(git branch --show-current)
    if [ "$branch" != "main" ] && [ "$branch" != "master" ]; then
        log_warn "Currently on branch '$branch'"
        read -p "Switch to main and merge? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            git checkout main
            git merge "$branch"
        else
            log_info "Staying on $branch. Push manually if needed."
        fi
    fi

    git push origin HEAD

    log_success "Pushed to GitHub!"
    log_info "GitHub Pages will update automatically if configured."
    log_info "Configure at: https://github.com/<user>/<repo>/settings/pages"
}

# ============================================
# Status
# ============================================
show_status() {
    echo ""
    echo "=========================================="
    echo "  Tumblr to Leaflet - Deployment Status"
    echo "=========================================="
    echo ""

    # Local server
    echo -n "Local Server:    "
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "${GREEN}Running${NC} (PID $pid) - http://localhost:$LOCAL_PORT"
        else
            echo -e "${YELLOW}Stale PID${NC} (not running)"
        fi
    else
        echo -e "${RED}Stopped${NC}"
    fi

    # Check if wrangler is available for worker status
    echo -n "Worker (CF):     "
    if command -v wrangler &>/dev/null || command -v npx &>/dev/null; then
        echo -e "${BLUE}Available${NC} - run 'deploy.sh worker' to deploy"
    else
        echo -e "${YELLOW}wrangler not installed${NC}"
    fi

    # Git status
    echo -n "Git Branch:      "
    cd "$PROJECT_ROOT"
    local branch=$(git branch --show-current)
    echo -e "${BLUE}$branch${NC}"

    echo ""
    echo "Commands:"
    echo "  ./scripts/deploy.sh local       # Start local server"
    echo "  ./scripts/deploy.sh stop        # Stop local server"
    echo "  ./scripts/deploy.sh worker      # Deploy CF Worker"
    echo "  ./scripts/deploy.sh worker-dev  # Run CF Worker locally"
    echo "  ./scripts/deploy.sh pages       # Push to GitHub Pages"
    echo ""
}

# ============================================
# Main
# ============================================
case "${1:-status}" in
    local|start)
        start_local
        ;;
    stop)
        stop_local
        ;;
    worker)
        deploy_worker
        ;;
    worker-dev)
        run_worker_dev
        ;;
    pages)
        deploy_pages
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {local|stop|worker|worker-dev|pages|status}"
        exit 1
        ;;
esac
