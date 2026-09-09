/**
 * How many people looked, and how many actually ran something.
 *
 * The record in `record.ts` answers what an examination DID; it says nothing
 * about how many people arrived and left without running one, which is the
 * number that says whether the sheet is understood. Its ring buffer also holds
 * only the last 500 entries, so it cannot answer "how many altogether" once a
 * round of testing exceeds that. These counters are cumulative and separate.
 *
 * WHAT IS NEVER COUNTED: addresses, user agents, referrers, countries,
 * anything that identifies a visitor or could be joined to one later. A view
 * is a path and a timestamp. That is the whole record, and it is deliberately
 * too little to build a profile from.
 *
 * THEY RESET WHEN THE INSTANCE SLEEPS. The free tier stops the process after
 * fifteen minutes idle and these live in memory, so the in-process totals are
 * "since this instance woke", not "ever". That is why every event also goes to
 * stdout as one line: the host's log viewer keeps those, and
 * tools/read_record.py counts them back from an exported log. The durable
 * answer is the log; this is the convenient one.
 */

/** A path worth counting. Everything else is an asset or an API call. */
const COUNTED_PATHS = new Set(['/', '/about']);

export type Outcome = 'examined' | 'rate-limited' | 'queue-full' | 'rejected' | 'failed';

const views = new Map<string, number>();
const examinations = new Map<string, number>();
const wokeAt = Date.now();

/**
 * Count one page view.
 *
 * Only GET, only a path the sheet actually serves, and only a 2xx: a 404 is
 * not a visit, and Render's health check hits an API route rather than a page
 * so it never lands here. HEAD is excluded because uptime pingers use it and
 * a pinger is not a reader.
 */
export function countView(method: string, pathname: string, status: number): void {
  if (method !== 'GET') return;
  if (status < 200 || status >= 300) return;
  const path = pathname.replace(/\/+$/, '') || '/';
  if (!COUNTED_PATHS.has(path)) return;
  views.set(path, (views.get(path) ?? 0) + 1);
  // One line, so the log viewer keeps it and `grep view ` finds it.
  console.log(`view ${JSON.stringify({ path, at: new Date().toISOString() })}`);
}

/**
 * Count one examination attempt, whatever became of it.
 *
 * Called BY `record()`, not alongside it. There are five places that record an
 * examination and there will be more; wiring a counter into each is how one
 * gets missed, and a tally that is silently short is worse than none. One call
 * site, impossible to forget. Refusals are counted
 * too: a visitor turned away by the rate limiter still arrived wanting an
 * answer, and a round where most attempts are `rate-limited` means something
 * quite different from one where most are `examined`.
 */
export function countExamination(outcome: Outcome): void {
  examinations.set(outcome, (examinations.get(outcome) ?? 0) + 1);
}

export function metrics() {
  const viewed = [...views.values()].reduce((n, v) => n + v, 0);
  const ran = [...examinations.values()].reduce((n, v) => n + v, 0);
  const examined = examinations.get('examined') ?? 0;
  const hours = (Date.now() - wokeAt) / 3_600_000;

  return {
    since: new Date(wokeAt).toISOString(),
    hoursAwake: Number(hours.toFixed(2)),
    views: viewed,
    viewsByPath: Object.fromEntries(views),
    examinationAttempts: ran,
    examinationsByOutcome: Object.fromEntries(examinations),
    /**
     * Of the people who looked, how many ran something. The number this was
     * built to answer, and the one that says whether the sheet explains
     * itself: a visitor who reads the plot and leaves has not been served.
     * Null rather than 0 when nobody has looked yet, because 0/0 is not a rate
     * and printing "0%" would invent a finding out of no data.
     */
    ranAfterViewing: viewed ? Number((ran / viewed).toFixed(3)) : null,
    completedAfterViewing: viewed ? Number((examined / viewed).toFixed(3)) : null,
    note:
      'Counted since this instance woke. The free tier sleeps after fifteen minutes ' +
      'idle and these live in memory; the durable copy is the stdout log, which ' +
      'tools/read_record.py counts back.',
  };
}

/** Test seam: forget everything. */
export function reset(): void {
  views.clear();
  examinations.clear();
}
