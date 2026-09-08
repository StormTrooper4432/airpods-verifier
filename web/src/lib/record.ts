/**
 * What happened, in the shape that can be measured. Nothing that identifies
 * anyone.
 *
 * Every real improvement in this project came from measuring a corpus, and a
 * test round with no record produces anecdotes instead. This exists to answer
 * questions that were previously unanswerable outside a labelled corpus, above
 * all one: the sheet now warns before an examination that cannot reach a
 * verdict, and nobody knows whether that changes what people photograph. It was
 * built on corpus data and has never been tested on a person.
 *
 * WHAT IS NEVER RECORDED: photographs, filenames, file sizes, serial numbers,
 * model numbers, any extracted text, IP addresses, user agents, or anything
 * derived from them. Panels are fixed enum values, verdicts are one of three
 * words, and weights are numbers the rule table already publishes.
 *
 * The session id is generated in the browser, kept in sessionStorage, and dies
 * with the tab. It exists only to tell a second attempt from a first person,
 * which is the whole behavioural question. It is not linked to an address and
 * cannot be traced to anyone.
 *
 * Records go to stdout as JSON lines, which the host's log viewer keeps without
 * any storage to configure, and to a small ring buffer that `/api/record` can
 * read back. That endpoint refuses to answer unless RECORD_TOKEN is set, so an
 * unconfigured deployment cannot leak it.
 */

import type { PanelId, Verdict } from '../data/sheet';

const ENABLED = process.env.RECORD_EXAMINATIONS !== 'off';
const KEEP = Math.max(50, Number(process.env.RECORD_KEEP ?? 500));

export type Outcome =
  | 'examined'
  | 'rate-limited'
  | 'queue-full'
  | 'rejected'
  | 'failed';

export interface Record {
  at: string;
  session: string;
  attempt: number;
  outcome: Outcome;
  /** Surfaces the visitor attached a photograph to. */
  offered: PanelId[];
  /** Surfaces the engine actually resolved. */
  read?: PanelId[];
  /** What the sheet predicted before the run, and whether it warned. */
  forecast?: number;
  warned?: boolean;
  reached?: number;
  verdict?: Verdict;
  score?: number;
  /** Checks that could not run, by name. No detail text. */
  unresolved?: string[];
  seconds?: number;
}

const buffer: Record[] = [];
const attempts = new Map<string, number>();

/** A session id that is a short opaque token or nothing. Never trusted as data. */
export function sessionOf(request: Request): string {
  const raw = request.headers.get('x-sheet-session') ?? '';
  return /^[a-z0-9]{6,32}$/i.test(raw) ? raw : 'anonymous';
}

export function nextAttempt(session: string): number {
  const n = (attempts.get(session) ?? 0) + 1;
  attempts.set(session, n);
  if (attempts.size > KEEP * 2) attempts.clear();
  return n;
}

export function record(entry: Omit<Record, 'at'>) {
  if (!ENABLED) return;
  const full: Record = { at: new Date().toISOString(), ...entry };
  buffer.push(full);
  if (buffer.length > KEEP) buffer.shift();
  // One line, so a log viewer keeps it and `grep examination` finds it.
  console.log(`examination ${JSON.stringify(full)}`);
}

export function all(): Record[] {
  return [...buffer];
}

/**
 * The question the coverage warning was built to answer.
 *
 * A session that was warned its photographs could not reach a verdict, and then
 * attached more surfaces on a later attempt, is the warning working. One that
 * ran again unchanged, or stopped, is not.
 */
export function warningEffect() {
  const sessions = new Map<string, Record[]>();
  for (const entry of buffer) {
    if (entry.session === 'anonymous') continue;
    sessions.set(entry.session, [...(sessions.get(entry.session) ?? []), entry]);
  }

  let warnedOnce = 0;
  let addedAfterWarning = 0;
  let retriedUnchanged = 0;
  let stoppedAfterWarning = 0;

  for (const entries of sessions.values()) {
    const ordered = [...entries].sort((a, b) => a.attempt - b.attempt);
    const firstWarned = ordered.findIndex((e) => e.warned);
    if (firstWarned === -1) continue;
    warnedOnce += 1;
    const after = ordered.slice(firstWarned + 1);
    if (!after.length) {
      stoppedAfterWarning += 1;
      continue;
    }
    const before = new Set(ordered[firstWarned]!.offered);
    if (after.some((e) => e.offered.some((panel) => !before.has(panel)))) {
      addedAfterWarning += 1;
    } else {
      retriedUnchanged += 1;
    }
  }

  return {
    sessions: sessions.size,
    warnedOnce,
    addedAfterWarning,
    retriedUnchanged,
    stoppedAfterWarning,
  };
}

export function summary() {
  const outcomes: Record_<string, number> = {};
  const verdicts: Record_<string, number> = {};
  const panelCounts: Record_<string, number> = {};
  let reached = 0;
  let examined = 0;

  for (const entry of buffer) {
    outcomes[entry.outcome] = (outcomes[entry.outcome] ?? 0) + 1;
    if (entry.verdict) verdicts[entry.verdict] = (verdicts[entry.verdict] ?? 0) + 1;
    for (const panel of entry.offered) panelCounts[panel] = (panelCounts[panel] ?? 0) + 1;
    if (entry.outcome === 'examined' && typeof entry.reached === 'number') {
      reached += entry.reached;
      examined += 1;
    }
  }

  return {
    records: buffer.length,
    outcomes,
    verdicts,
    panelsOffered: panelCounts,
    meanWeightReached: examined ? Number((reached / examined).toFixed(2)) : null,
    warning: warningEffect(),
  };
}

/** Local alias so the exported interface name can stay `Record`. */
type Record_<K extends string, V> = { [key in K]?: V };
