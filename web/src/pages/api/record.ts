/**
 * Read the anonymous record back.
 *
 * Fails closed: with no RECORD_TOKEN set the endpoint refuses rather than
 * exposing anything, so a deployment that never configured it cannot leak by
 * omission. The token is compared in constant time — it is a bearer secret,
 * and an early-exit comparison leaks it a character at a time.
 */
import type { APIRoute } from 'astro';
import { timingSafeEqual } from 'node:crypto';

import { all, summary } from '../../lib/record';

function authorised(request: Request): boolean {
  const expected = process.env.RECORD_TOKEN;
  if (!expected) return false;
  const offered =
    request.headers.get('authorization')?.replace(/^Bearer\s+/i, '') ??
    new URL(request.url).searchParams.get('token') ??
    '';
  const a = Buffer.from(offered);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export const GET: APIRoute = async ({ request }) => {
  if (!authorised(request)) {
    return new Response(
      JSON.stringify({
        problem: 'Not available.',
        recovery: 'Set RECORD_TOKEN in the server environment and pass it as a bearer token.',
      }),
      { status: 404, headers: { 'content-type': 'application/json' } },
    );
  }
  const full = new URL(request.url).searchParams.has('full');
  return new Response(JSON.stringify(full ? { ...summary(), records: all() } : summary(), null, 2), {
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
};
