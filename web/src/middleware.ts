/**
 * Same-origin check that understands a proxy.
 *
 * Astro's built-in `checkOrigin` compares the `Origin` header against the URL
 * the server itself sees. Behind Render (or Cloud Run, or any load balancer)
 * that URL is `http://localhost:8080`, while the browser sends the public
 * `https://…` origin, so every form post is rejected as cross-site. The
 * symptom is a deployed sheet that renders perfectly and refuses to examine
 * anything — invisible in local testing and invisible to CI, because neither
 * has a proxy in front.
 *
 * So Astro's version is turned off in the config and this replaces it. The
 * difference is that this reconstructs the expected origin from the forwarded
 * headers the proxy actually sets, and compares hosts rather than full URLs.
 *
 * It is not weaker than what it replaces. There is no session and no cookie
 * here, so the worst a cross-site post could do is spend quota — which the
 * rate limiter bounds independently, by address.
 */
import type { MiddlewareHandler } from 'astro';

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/** The host a browser thinks it is talking to, as the proxy reports it. */
function publicHost(request: Request, url: URL): string | null {
  const forwarded = request.headers.get('x-forwarded-host');
  if (forwarded) return forwarded.split(',')[0]!.trim().toLowerCase();
  const host = request.headers.get('host');
  if (host) return host.trim().toLowerCase();
  return url.host ? url.host.toLowerCase() : null;
}

export const onRequest: MiddlewareHandler = async (context, next) => {
  const { request } = context;
  if (!UNSAFE.has(request.method)) return next();

  const origin = request.headers.get('origin');

  // A form post from a plain HTML page sends no Origin in some browsers. The
  // Sec-Fetch-Site header is the modern signal, and where neither exists there
  // is nothing to check against, which is the same position Astro's own check
  // takes rather than blocking outright.
  const site = request.headers.get('sec-fetch-site');
  if (site && site !== 'same-origin' && site !== 'none') {
    return new Response('Cross-site request refused.', { status: 403 });
  }
  if (!origin) return next();

  const expected = publicHost(request, new URL(request.url));
  let sent: string;
  try {
    sent = new URL(origin).host.toLowerCase();
  } catch {
    return new Response('Malformed Origin header.', { status: 403 });
  }

  if (expected && sent !== expected) {
    return new Response('Cross-site request refused.', { status: 403 });
  }
  return next();
};
