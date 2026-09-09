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
import { countExamination } from './metrics.ts';

const ENABLED = process.env.RECORD_EXAMINATIONS !== 'off';
const KEEP = Math.max(50, Number(process.env.RECORD_KEEP ?? 500));

export type Outcome =
  | 'examined'
  | 'rate-limited'
  | 'queue-full'
  | 'rejected'
  | 'failed';

export interface Record {
  /** Short handle so an examination can be labelled afterwards. */
  id: string;
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
  /**
   * Checks that FAILED, by name. The single most useful field for reading a
   * live round: it says what the tool is actually deciding on out there, which
   * on the corpus turned out to be a much narrower set of rules than the nine
   * suggest. Names only, never the finding text - a detail line can quote a
   * serial.
   */
  failed?: string[];
  seconds?: number;
  /**
   * What it actually was, supplied by the owner after the fact.
   *
   * The record deliberately keeps no photographs, so a labelled entry cannot
   * be re-scored against a future rule the way the offline corpus can. What it
   * does give is a live confusion matrix for the rules as they stand - and the
   * two numbers that have governed every decision here are exactly the ones it
   * answers: did a counterfeit pass, and was a genuine unit accused.
   */
  truth?: 'GENUINE' | 'COUNTERFEIT';
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

export function record(entry: Omit<Record, 'at' | 'id'>) {
  // Counted even when recording is switched off: the tally is anonymous in a
  // way the record is not, and turning off the detail should not blind the
  // count of how many people ran something.
  countExamination(entry.outcome);
  if (!ENABLED) return;
  const full: Record = {
    id: Math.random().toString(36).slice(2, 8),
    at: new Date().toISOString(),
    ...entry,
  };
  buffer.push(full);
  if (buffer.length > KEEP) buffer.shift();
  // One line, so a log viewer keeps it and `grep examination` finds it.
  console.log(`examination ${JSON.stringify(full)}`);
}

export function all(): Record[] {
  return [...buffer];
}

/** Attach the real answer to one recorded examination. */
export function label(id: string, truth: 'GENUINE' | 'COUNTERFEIT'): Record | null {
  const entry = buffer.find((candidate) => candidate.id === id);
  if (!entry) return null;
  entry.truth = truth;
  console.log(`examination-label ${JSON.stringify({ id, truth })}`);
  return entry;
}

/**
 * The two numbers that have governed every decision in this project, measured
 * on whatever has actually been labelled.
 */
export function agreement() {
  const labelled = buffer.filter((entry) => entry.truth && entry.verdict);
  if (!labelled.length) return { labelled: 0 };
  const count = (truth: string, verdict: string) =>
    labelled.filter((e) => e.truth === truth && e.verdict === verdict).length;
  return {
    labelled: labelled.length,
    counterfeitPassedAsGenuine: count('COUNTERFEIT', 'GENUINE'),
    genuineAccused: count('GENUINE', 'HIGH_RISK_SUSPECTED'),
    counterfeitCaught: count('COUNTERFEIT', 'HIGH_RISK_SUSPECTED'),
    genuineCleared: count('GENUINE', 'GENUINE'),
    inconclusive: labelled.filter((e) => e.verdict === 'INCONCLUSIVE').length,
  };
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

/**
 * Is the forecast honest?
 *
 * The sheet tells a visitor what their photographs can reach before it runs,
 * counted from the surface they attached each one to. The engine then reads the
 * photographs themselves and can resolve different surfaces - a label caught in
 * the corner of another shot counts, a blurred one does not. The very first
 * live run forecast 4.5 and reached 10.5, so the error is not small and it is
 * not one-sided.
 *
 * A forecast that systematically undershoots talks people out of examinations
 * that would have worked. One that overshoots promises a verdict the engine
 * refuses. Both are worth knowing before anyone trusts the number.
 */
export function forecastAccuracy() {
  const pairs = buffer.filter(
    (entry) =>
      entry.outcome === 'examined' &&
      typeof entry.forecast === 'number' &&
      typeof entry.reached === 'number',
  );
  if (!pairs.length) return { runs: 0 };

  const errors = pairs.map((entry) => entry.reached! - entry.forecast!);
  const sorted = [...errors].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)]!;
  return {
    runs: pairs.length,
    exact: errors.filter((e) => e === 0).length,
    undershot: errors.filter((e) => e > 0).length,
    overshot: errors.filter((e) => e < 0).length,
    medianError: Number(median.toFixed(2)),
    worstUnder: Number(Math.max(0, ...errors).toFixed(2)),
    worstOver: Number(Math.min(0, ...errors).toFixed(2)),
  };
}

export function summary() {
  const outcomes: Record_<string, number> = {};
  const verdicts: Record_<string, number> = {};
  const panelCounts: Record_<string, number> = {};
  const failures: Record_<string, number> = {};
  let reached = 0;
  let examined = 0;

  for (const entry of buffer) {
    outcomes[entry.outcome] = (outcomes[entry.outcome] ?? 0) + 1;
    if (entry.verdict) verdicts[entry.verdict] = (verdicts[entry.verdict] ?? 0) + 1;
    for (const panel of entry.offered) panelCounts[panel] = (panelCounts[panel] ?? 0) + 1;
    for (const rule of entry.failed ?? []) failures[rule] = (failures[rule] ?? 0) + 1;
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
    rulesThatFailed: failures,
    meanWeightReached: examined ? Number((reached / examined).toFixed(2)) : null,
    forecast: forecastAccuracy(),
    agreement: agreement(),
    warning: warningEffect(),
  };
}

/** Local alias so the exported interface name can stay `Record`. */
type Record_<K extends string, V> = { [key in K]?: V };
