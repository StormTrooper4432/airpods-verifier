import { claim, refund, reset, budget, callerAddress } from '../src/lib/limits.ts';
let pass = 0, fail = 0;
const ok = (cond, name) => { cond ? pass++ : (fail++, console.log('  FAIL:', name)); };

reset();
// hourly window
for (let i = 0; i < 6; i++) ok(claim('1.1.1.1').allowed, `call ${i+1} allowed`);
const seventh = claim('1.1.1.1');
ok(!seventh.allowed, 'seventh in an hour refused');
ok(/6 examinations from here within the hour/.test(seventh.problem), 'names the limit');
ok(seventh.retryAfter > 0 && seventh.retryAfter <= 3600, 'says when to come back');

// a different caller is unaffected
reset();
for (let i = 0; i < 6; i++) claim('1.1.1.1');
ok(claim('2.2.2.2').allowed, 'a different address is not punished');

// daily budget is the backstop, independent of address
reset();
let refused = null;
for (let i = 0; i < 400 && !refused; i++) {
  const a = claim(`10.0.${Math.floor(i/250)}.${i%250}`);
  if (!a.allowed) refused = a;
}
ok(refused !== null, 'the daily budget stops a flood of new addresses');
ok(/used up/.test(refused?.problem ?? ''), 'says the allowance is spent');
ok(budget().spent === budget().of, `budget exhausted exactly at ${budget().of}`);

// refund
reset();
claim('3.3.3.3'); const before = budget().spent;
refund('3.3.3.3');
ok(budget().spent === before - 1, 'a refund releases the budget');

// address extraction
const req = (h) => new Request('https://x/', { headers: h });
ok(callerAddress(req({'x-forwarded-for':'9.9.9.9, 10.1.1.1, 172.16.0.1'})) === '9.9.9.9',
   'takes the client, not the proxy');
ok(callerAddress(req({'x-real-ip':'8.8.8.8'})) === '8.8.8.8', 'falls back to x-real-ip');
ok(callerAddress(req({}), '7.7.7.7') === '7.7.7.7', 'falls back to the socket address');
ok(callerAddress(req({})) === 'unknown', 'never throws when nothing is known');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
