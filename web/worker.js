/**
 * Cloudflare Worker - Tumblr RSS Proxy
 *
 * This worker proxies requests to Tumblr RSS feeds,
 * adding CORS headers to allow browser access.
 *
 * Deploy: wrangler deploy worker.js --name tumblr-proxy
 */

export default {
  async fetch(request, env, ctx) {
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

    const url = new URL(request.url);
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

    // Only allow Tumblr domains for security
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

    if (!parsedTarget.hostname.endsWith('.tumblr.com')) {
      return new Response(JSON.stringify({ error: 'Only tumblr.com domains allowed' }), {
        status: 403,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': '*',
        },
      });
    }

    try {
      // Fetch from Tumblr
      const response = await fetch(targetUrl, {
        headers: {
          'User-Agent': 'TumblrToLeaflet/1.0',
          'Accept': 'application/rss+xml, application/xml, text/xml, */*',
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
