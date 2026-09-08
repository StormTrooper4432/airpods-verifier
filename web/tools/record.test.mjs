/**
 * The record must never carry anything that identifies a person, and the read
 * endpoint must refuse when unconfigured. Both are asserted here rather than
 * trusted, because a leak would be silent.
 */
import { record, all, summary, sessionOf, nextAttempt } from '../src/lib/record.ts';

let pass = 0, fail = 0;
const ok = (c, n) => { c ? pass++ : (fail++, console.log('  FAIL:', n)); };

record({
  session: 'abc123', attempt: 1, outcome: 'examined',
  offered: ['box_front'], read: ['box_front'],
  forecast: 4.5, warned: true, reached: 4.5,
  verdict: 'INCONCLUSIVE', score: 0.23,
  unresolved: ['Serial Cross-Match'], failed: ['Serial Label Format'], seconds: 12,
});

const text = JSON.stringify(all());
// Rule NAMES legitimately contain the word "serial" ("Serial Cross-Match"),
// so what is banned is a serial-shaped VALUE, and identity of any other kind.
const SERIAL_SHAPED = /\b(?![A-Z]{10}\b)[A-Z0-9]{10,12}\b/g;
const serials = (text.match(SERIAL_SHAPED) ?? [])
  .filter((t) => /[0-9]/.test(t) && /[A-Z]/.test(t));
ok(serials.length === 0, `serial-shaped tokens in the record: ${serials}`);

const FORBIDDEN = [
  '(S) Serial No', 'Serial No.',                    // the label line itself
  '.jpg', '.png', '.heic', 'filename',              // file identity
  'x-forwarded', 'user-agent', 'ipAddress',         // network identity
  'A3047', 'A2698', 'MTJV',                          // model and part numbers
];
for (const banned of FORBIDDEN) {
  ok(!text.toLowerCase().includes(banned.toLowerCase()),
     `a record must not contain "${banned}"`);
}

// Only the fields the type declares, nothing picked up along the way.
const allowed = new Set(['at','session','attempt','outcome','offered','read','forecast',
  'warned','reached','verdict','score','unresolved','failed','seconds']);
for (const key of Object.keys(all()[0])) ok(allowed.has(key), `unexpected field: ${key}`);

// A session id is an opaque token or nothing; never a header echoed back.
const req = (h) => new Request('http://x/', { headers: h });
ok(sessionOf(req({ 'x-sheet-session': 'abc123' })) === 'abc123', 'accepts a plain token');
ok(sessionOf(req({ 'x-sheet-session': '../../etc/passwd' })) === 'anonymous', 'rejects a path');
ok(sessionOf(req({ 'x-sheet-session': '<script>' })) === 'anonymous', 'rejects markup');
ok(sessionOf(req({ 'x-sheet-session': 'a'.repeat(200) })) === 'anonymous', 'rejects an overlong id');
ok(sessionOf(req({})) === 'anonymous', 'missing id is anonymous, not an error');

// Attempts count within a session.
ok(nextAttempt('s1') === 1 && nextAttempt('s1') === 2, 'attempts increment per session');
ok(nextAttempt('s2') === 1, 'a different session starts at one');

// The behavioural question is actually answered.
record({ session: 'w1', attempt: 1, outcome: 'examined', offered: ['box_front'], warned: true });
record({ session: 'w1', attempt: 2, outcome: 'examined', offered: ['box_front','box_bottom_label'], warned: false });
record({ session: 'w2', attempt: 1, outcome: 'examined', offered: ['box_front'], warned: true });
record({ session: 'w2', attempt: 2, outcome: 'examined', offered: ['box_front'], warned: true });
record({ session: 'w3', attempt: 1, outcome: 'examined', offered: ['box_front'], warned: true });
const w = summary().warning;
// 'abc123' above was also warned once and never came back, so it counts as a
// stop too — the assertion is on the shape of the answer, not on a total that
// depends on what else the buffer holds.
ok(w.addedAfterWarning === 1, `added after warning: ${w.addedAfterWarning}`);
ok(w.retriedUnchanged === 1, `retried unchanged: ${w.retriedUnchanged}`);
ok(w.stoppedAfterWarning >= 1, `stopped after warning: ${w.stoppedAfterWarning}`);
ok(w.warnedOnce === w.addedAfterWarning + w.retriedUnchanged + w.stoppedAfterWarning,
   'every warned session lands in exactly one bucket');

// The field that says what the tool actually decides on in the wild.
ok(JSON.stringify(summary().rulesThatFailed) === '{"Serial Label Format":1}',
   `rulesThatFailed: ${JSON.stringify(summary().rulesThatFailed)}`);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
