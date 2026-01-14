#!/bin/bash
#
# Deploy script for Tumblr to Leaflet web app
# Usage: ./scripts/deploy.sh [command]
#
# Commands:
#   dev         - Start local server + worker (full local dev)
#   local       - Start local development server only
#   stop        - Stop all local services
#   web         - Build and deploy web to CF Pages (with version)
#   worker      - Deploy Cloudflare Worker (with version)
#   build       - Build web locally (for testing)
#   worker-dev  - Run Cloudflare Worker locally only
#   pages       - Push to GitHub
#   status      - Show status of all services
#

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$PROJECT_ROOT/web"
PID_FILE="$PROJECT_ROOT/.local-server.pid"
WORKER_PID_FILE="$PROJECT_ROOT/.local-worker.pid"
LOCAL_PORT=8080
WORKER_PORT=8787

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
# Version Injection
# ============================================
get_build_version() {
    local hash=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    local date=$(date -u +"%Y-%m-%d %H:%M UTC")
    echo "${hash} (${date})"
}

inject_version() {
    local file="$1"
    local version=$(get_build_version)

    if [ ! -f "$file" ]; then
        log_error "File not found: $file"
        return 1
    fi

    # Replace placeholder with version (supports both formats)
    if grep -q "__BUILD_VERSION__\|>dev<" "$file"; then
        sed -i.bak "s/__BUILD_VERSION__/${version}/g" "$file"
        sed -i.bak "s/>dev</>$version</g" "$file"
        rm -f "${file}.bak"
        log_info "Injected version '${version}' into $(basename $file)"
    else
        log_warn "No version placeholder found in $(basename $file)"
    fi
}

restore_placeholder() {
    local file="$1"
    local version=$(get_build_version)

    if [ -f "$file" ]; then
        # Restore placeholder for git cleanliness
        sed -i.bak "s/${version}/__BUILD_VERSION__/g" "$file"
        rm -f "${file}.bak"
    fi
}

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

    # Inject version before deploy
    inject_version "$WEB_DIR/worker.js"

    local deploy_result=0
    if command -v wrangler &>/dev/null; then
        wrangler deploy || deploy_result=$?
    else
        npx wrangler deploy || deploy_result=$?
    fi

    # Restore placeholder after deploy (keep git clean)
    restore_placeholder "$WEB_DIR/worker.js"

    if [ $deploy_result -eq 0 ]; then
        log_success "Worker deployed with version: $(get_build_version)"
        log_info "URL: https://tumblr-proxy.<your-subdomain>.workers.dev"
        log_info "Version endpoint: https://tumblr-proxy.<your-subdomain>.workers.dev/version"
    else
        log_error "Worker deployment failed!"
        exit 1
    fi
}

start_worker_dev() {
    log_info "Starting Cloudflare Worker locally..."

    if [ -f "$WORKER_PID_FILE" ]; then
        local pid=$(cat "$WORKER_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Worker already running on PID $pid"
            log_info "URL: http://localhost:$WORKER_PORT"
            return 0
        fi
    fi

    cd "$WEB_DIR"

    if command -v wrangler &>/dev/null; then
        wrangler dev --port $WORKER_PORT &
    else
        npx wrangler dev --port $WORKER_PORT &
    fi

    local pid=$!
    echo $pid > "$WORKER_PID_FILE"

    # Wait for worker to be ready
    sleep 3
    if kill -0 "$pid" 2>/dev/null; then
        log_success "Worker started on PID $pid"
        log_info "URL: http://localhost:$WORKER_PORT"
    else
        log_error "Failed to start worker"
        rm -f "$WORKER_PID_FILE"
        return 1
    fi
}

stop_worker_dev() {
    log_info "Stopping local worker..."

    if [ -f "$WORKER_PID_FILE" ]; then
        local pid=$(cat "$WORKER_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            # Also kill child processes (wrangler spawns children)
            pkill -P "$pid" 2>/dev/null || true
            rm -f "$WORKER_PID_FILE"
            log_success "Worker stopped (PID $pid)"
        else
            log_warn "Worker not running (stale PID file)"
            rm -f "$WORKER_PID_FILE"
        fi
    else
        # Try to find and kill any wrangler process on our port
        local pids=$(lsof -ti:$WORKER_PORT 2>/dev/null || true)
        if [ -n "$pids" ]; then
            log_info "Found process on port $WORKER_PORT, killing..."
            echo "$pids" | xargs kill 2>/dev/null || true
            log_success "Killed processes on port $WORKER_PORT"
        fi
    fi
}

run_worker_dev() {
    # Interactive mode - runs in foreground
    log_info "Starting Cloudflare Worker in dev mode (interactive)..."

    cd "$WEB_DIR"

    if command -v wrangler &>/dev/null; then
        wrangler dev --port $WORKER_PORT
    else
        npx wrangler dev --port $WORKER_PORT
    fi
}

# ============================================
# Web Build (for Cloudflare Pages)
# ============================================
build_web() {
    log_info "Building web app with version injection..."

    local version=$(get_build_version)
    local dist_dir="$PROJECT_ROOT/dist"

    # Create dist directory
    rm -rf "$dist_dir"
    mkdir -p "$dist_dir"

    # Copy files
    cp "$WEB_DIR/index.html" "$dist_dir/"
    cp "$WEB_DIR/_headers" "$dist_dir/" 2>/dev/null || true
    cp -r "$WEB_DIR/images" "$dist_dir/" 2>/dev/null || true

    # Inject version
    inject_version "$dist_dir/index.html"

    log_success "Web built to dist/ with version: $version"
    log_info "Files in dist/:"
    ls -la "$dist_dir"
}

deploy_web() {
    log_info "Deploying web app to Cloudflare Pages..."

    # Build first
    build_web

    # Deploy to CF Pages
    cd "$PROJECT_ROOT"
    if command -v wrangler &>/dev/null; then
        wrangler pages deploy dist --project-name leafletimporter
    else
        npx wrangler pages deploy dist --project-name leafletimporter
    fi

    log_success "Web deployed to Cloudflare Pages!"
    log_info "URL: https://leafletimporter.pages.dev"
}

# ============================================
# Full Development Environment
# ============================================
start_dev() {
    log_info "Starting full development environment..."
    echo ""

    start_local
    echo ""
    start_worker_dev

    echo ""
    echo "=========================================="
    log_success "Development environment ready!"
    echo "=========================================="
    echo ""
    echo "  Web App:  http://localhost:$LOCAL_PORT"
    echo "  Worker:   http://localhost:$WORKER_PORT"
    echo ""
    echo "  Stop all: ./scripts/deploy.sh stop"
    echo ""
}

stop_all() {
    stop_local
    stop_worker_dev
    log_success "All services stopped"
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

    # Local worker
    echo -n "Local Worker:    "
    if [ -f "$WORKER_PID_FILE" ]; then
        local wpid=$(cat "$WORKER_PID_FILE")
        if kill -0 "$wpid" 2>/dev/null; then
            echo -e "${GREEN}Running${NC} (PID $wpid) - http://localhost:$WORKER_PORT"
        else
            echo -e "${YELLOW}Stale PID${NC} (not running)"
        fi
    else
        # Check if something is on the port anyway
        if lsof -ti:$WORKER_PORT &>/dev/null; then
            echo -e "${GREEN}Running${NC} (port $WORKER_PORT in use)"
        else
            echo -e "${RED}Stopped${NC}"
        fi
    fi

    # Check if wrangler is available for worker status
    echo -n "Wrangler:        "
    if command -v wrangler &>/dev/null || command -v npx &>/dev/null; then
        echo -e "${GREEN}Available${NC}"
    else
        echo -e "${YELLOW}Not installed${NC} (npm i -g wrangler)"
    fi

    # Git status
    echo -n "Git Branch:      "
    cd "$PROJECT_ROOT"
    local branch=$(git branch --show-current)
    echo -e "${BLUE}$branch${NC}"

    echo ""
    echo "Commands:"
    echo "  ./scripts/deploy.sh dev         # Start all (server + worker)"
    echo "  ./scripts/deploy.sh stop        # Stop all local services"
    echo "  ./scripts/deploy.sh local       # Start web server only"
    echo "  ./scripts/deploy.sh worker-dev  # Run worker (interactive)"
    echo "  ./scripts/deploy.sh web         # Deploy web to CF Pages"
    echo "  ./scripts/deploy.sh worker      # Deploy worker to CF"
    echo ""
}

# ============================================
# Main
# ============================================
case "${1:-status}" in
    dev)
        start_dev
        ;;
    local|start)
        start_local
        ;;
    stop)
        stop_all
        ;;
    worker)
        deploy_worker
        ;;
    worker-dev)
        run_worker_dev
        ;;
    worker-start)
        start_worker_dev
        ;;
    worker-stop)
        stop_worker_dev
        ;;
    pages)
        deploy_pages
        ;;
    build)
        build_web
        ;;
    web)
        deploy_web
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {dev|local|stop|worker|web|worker-dev|pages|build|status}"
        exit 1
        ;;
esac
