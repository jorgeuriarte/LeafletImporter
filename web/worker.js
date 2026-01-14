/**
 * Cloudflare Worker - Tumblr RSS Proxy
 *
 * This worker proxies requests to Tumblr RSS feeds,
 * adding CORS headers to allow browser access.
 *
 * Deploy: wrangler deploy worker.js --name tumblr-proxy
 */

const BUILD_VERSION = '__BUILD_VERSION__';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Version endpoint
    if (url.pathname === '/version') {
      return new Response(JSON.stringify({
        version: BUILD_VERSION,
        service: 'tumblr-proxy'
      }), {
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
          'Access-Control-Max-Age': '86400',
        },
      });
    }

    // Only allow GET requests
    if (request.method !== 'GET') {
      return new Response('Method not allowed', { status: 405 });
    }

    const targetUrl = url.searchParams.get('url');

    // Validate target URL
    if (!targetUrl) {
      return new Response(JSON.stringify({ error: 'Missing url parameter' }), {
        status: 400,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    // Validate and parse target URL
    let parsedTarget;
    try {
      parsedTarget = new URL(targetUrl);
    } catch {
      return new Response(JSON.stringify({ error: 'Invalid URL' }), {
        status: 400,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    // Allow Tumblr domains and common image CDNs used by Tumblr
    const allowedDomains = [
      '.tumblr.com',
      'media.tumblr.com',
      '64.media.tumblr.com',
      '66.media.tumblr.com',
      'assets.tumblr.com',
    ];

    const isAllowed = allowedDomains.some(domain => {
      if (domain.startsWith('.')) {
        return parsedTarget.hostname.endsWith(domain);
      }
      return parsedTarget.hostname === domain || parsedTarget.hostname.endsWith('.' + domain);
    });

    if (!isAllowed) {
      return new Response(JSON.stringify({ error: 'Only tumblr.com domains allowed' }), {
        status: 403,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    // Determine if this is an image request
    const isImageRequest = /\.(jpg|jpeg|png|gif|webp)($|\?)/i.test(parsedTarget.pathname) ||
      parsedTarget.hostname.includes('media.tumblr.com');

    try {
      // Fetch from Tumblr
      const response = await fetch(targetUrl, {
        headers: {
          'User-Agent': 'TumblrToLeaflet/1.0',
          'Accept': isImageRequest
            ? 'image/webp,image/png,image/jpeg,image/gif,*/*'
            : 'application/rss+xml, application/xml, text/xml, */*',
        },
      });

      if (!response.ok) {
        return new Response(JSON.stringify({
          error: `Tumblr returned ${response.status}`,
          status: response.status
        }), {
          status: response.status,
          headers: {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
          },
        });
      }

      // For images, return binary data
      if (isImageRequest) {
        const imageData = await response.arrayBuffer();
        const contentType = response.headers.get('Content-Type') || 'image/jpeg';

        return new Response(imageData, {
          status: 200,
          headers: {
            'Content-Type': contentType,
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=86400', // Cache images for 24 hours
          },
        });
      }

      // For RSS/XML, return text
      const body = await response.text();

      // Return with CORS headers
      return new Response(body, {
        status: 200,
        headers: {
          'Content-Type': response.headers.get('Content-Type') || 'application/xml',
          'Access-Control-Allow-Origin': '*',
          'Cache-Control': 'public, max-age=300', // Cache for 5 minutes
        },
      });

    } catch (error) {
      return new Response(JSON.stringify({
        error: 'Failed to fetch from Tumblr',
        details: error.message
      }), {
        status: 500,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }
  },
};
