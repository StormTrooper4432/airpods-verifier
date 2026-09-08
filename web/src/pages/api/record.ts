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

import { all, label, summary } from '../../lib/record';

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

/**
 * Attach the real answer to a recorded examination.
 *
 *   curl -X POST "$URL/api/record?token=…" \
 *        -H 'content-type: application/json' \
 *        -d '{"id":"a1b2c3","truth":"COUNTERFEIT"}'
 *
 * Owner-only, behind the same token as reading. Nothing a visitor sends can
 * label anything: this is the expert's judgement, and the whole value of the
 * offline corpus came from that judgement being separate from the pipeline's.
 */
export const POST: APIRoute = async ({ request }) => {
  if (!authorised(request)) {
    return new Response(JSON.stringify({ problem: 'Not available.' }), {
      status: 404,
      headers: { 'content-type': 'application/json' },
    });
  }

  let body: { id?: unknown; truth?: unknown };
  try {
    body = await request.json();
  } catch {
    return new Response(
      JSON.stringify({ problem: 'Expected JSON with an id and a truth.' }),
      { status: 400, headers: { 'content-type': 'application/json' } },
    );
  }

  const id = typeof body.id === 'string' ? body.id : '';
  const truth = body.truth;
  if (truth !== 'GENUINE' && truth !== 'COUNTERFEIT') {
    return new Response(
      JSON.stringify({
        problem: 'truth must be exactly "GENUINE" or "COUNTERFEIT".',
        recovery: 'Anything a rule cannot be scored against is not a label.',
      }),
      { status: 400, headers: { 'content-type': 'application/json' } },
    );
  }

  const entry = label(id, truth);
  if (!entry) {
    return new Response(
      JSON.stringify({
        problem: `No examination ${id} is still held.`,
        recovery: 'Records are kept in memory and are lost when the instance sleeps.',
      }),
      { status: 404, headers: { 'content-type': 'application/json' } },
    );
  }
  return new Response(JSON.stringify({ id: entry.id, truth: entry.truth, verdict: entry.verdict }), {
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
};
