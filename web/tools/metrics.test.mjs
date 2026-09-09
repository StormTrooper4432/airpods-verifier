/**
 * The traffic counters.
 *
 * A tally that is silently short is worse than no tally: it reads as a fact
 * and it is a guess. These pin what does and does not count, because every one
 * of those decisions is invisible once the number is printed.
 */
import { countView, countExamination, metrics, reset } from '../src/lib/metrics.ts';

let pass = 0, fail = 0;
const ok = (cond, label) => {
  if (cond) { pass += 1; console.log(`  ok   ${label}`); }
  else { fail += 1; console.log(`  FAIL ${label}`); }
};

console.log('\nwhat counts as a view');
reset();
countView('GET', '/', 200);
countView('GET', '/about', 200);
ok(metrics().views === 2, 'a served page counts');
ok(metrics().viewsByPath['/'] === 1, 'counted against its path');

reset();
countView('GET', '/', 404);
countView('GET', '/', 500);
ok(metrics().views === 0, 'a 404 or a 500 is not a visit');

reset();
countView('POST', '/', 200);
ok(metrics().views === 0, 'a form post is not a view');

reset();
countView('HEAD', '/', 200);
ok(metrics().views === 0, 'HEAD is an uptime pinger, not a reader');

reset();
countView('GET', '/api/examine', 200);
countView('GET', '/api/record', 200);
ok(metrics().views === 0, 'API calls are not page views');
// Render's health check hits /api/examine every few minutes. If that counted,
// the view total would be dominated by the host polling itself.

reset();
countView('GET', '/_astro/index.abc123.js', 200);
countView('GET', '/mark.svg', 200);
ok(metrics().views === 0, 'assets are not page views');

reset();
countView('GET', '/', 200);
countView('GET', '//', 200);
ok(metrics().views === 2 && Object.keys(metrics().viewsByPath).length === 1,
   'a trailing slash is the same page, not a second one');

console.log('\nexamination counting');
reset();
for (const o of ['examined', 'examined', 'rate-limited', 'failed']) countExamination(o);
ok(metrics().examinationAttempts === 4, 'every attempt counts, including refusals');
ok(metrics().examinationsByOutcome.examined === 2, 'outcomes are kept apart');
// A round that is mostly rate-limited means something very different from one
// that is mostly examined; collapsing them would hide it.
ok(metrics().examinationsByOutcome['rate-limited'] === 1, 'a refusal is still an arrival');

console.log('\nthe rate this exists to answer');
reset();
for (let i = 0; i < 10; i++) countView('GET', '/', 200);
countExamination('examined');
countExamination('rate-limited');
ok(metrics().ranAfterViewing === 0.2, 'ran-after-viewing counts attempts');
ok(metrics().completedAfterViewing === 0.1, 'completed-after-viewing counts only successes');

reset();
ok(metrics().ranAfterViewing === null, 'no visitors yet is null, not zero');
// 0/0 is not a rate. Printing "0%" would state a finding where there is no
// data, which is the whole failure mode this project exists to avoid.

console.log('\nwhat is never recorded');
reset();
countView('GET', '/', 200);
const shape = JSON.stringify(metrics());
for (const banned of ['ip', 'address', 'agent', 'referr', 'country', 'session']) {
  ok(!shape.toLowerCase().includes(banned + '"') && !shape.toLowerCase().includes(`"${banned}`),
     `no ${banned} field reaches the output`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
