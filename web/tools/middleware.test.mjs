import { onRequest } from '../src/middleware.ts';

let pass = 0, fail = 0;
const ok = (c, n) => { c ? pass++ : (fail++, console.log('  FAIL:', n)); };
const next = async () => new Response('ok', { status: 200 });

async function status(method, headers, url = 'http://localhost:8080/api/examine') {
  const request = new Request(url, { method, headers, ...(method === 'POST' ? { body: 'x' } : {}) });
  const res = await onRequest({ request }, next);
  return res.status;
}

// A browser talking straight to the server.
ok(await status('POST', { origin: 'http://localhost:8080', host: 'localhost:8080' }) === 200,
   'same-origin post is allowed');

// Render, Cloud Run, any load balancer: the server sees localhost, the browser
// sees https. This is the case that broke the deployed sheet.
ok(await status('POST', {
  origin: 'https://airpods-verifier.onrender.com',
  host: 'localhost:8080',
  'x-forwarded-host': 'airpods-verifier.onrender.com',
  'x-forwarded-proto': 'https',
}) === 200, 'proxied post is allowed on the forwarded host');

// The protocol differing must not matter; only the host does.
ok(await status('POST', {
  origin: 'https://example.test', host: 'localhost:8080', 'x-forwarded-host': 'example.test',
}) === 200, 'http/https mismatch alone does not refuse');

// Real cross-site attempts.
ok(await status('POST', {
  origin: 'https://evil.example', 'x-forwarded-host': 'airpods-verifier.onrender.com',
}) === 403, 'a different origin is refused');
ok(await status('POST', { 'sec-fetch-site': 'cross-site' }) === 403,
   'Sec-Fetch-Site cross-site is refused even with no Origin');
ok(await status('POST', { origin: 'not a url', host: 'localhost:8080' }) === 403,
   'a malformed Origin is refused');

// A form post with no Origin at all is not blocked, matching what it replaced.
ok(await status('POST', { host: 'localhost:8080' }) === 200, 'no Origin is not blocked');
ok(await status('POST', { 'sec-fetch-site': 'same-origin', host: 'localhost:8080' }) === 200,
   'same-origin fetch metadata is allowed');

// Reads are never checked.
ok(await status('GET', { origin: 'https://evil.example' }) === 200, 'GET is untouched');

// The first forwarded host wins when a chain of proxies appends.
ok(await status('POST', {
  origin: 'https://a.test', 'x-forwarded-host': 'a.test, b.internal',
}) === 200, 'takes the first forwarded host, not the last');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
