/**
 * One examination at a time, with an honest place in line.
 *
 * Every request used to spawn a pipeline immediately. On a free tier that is
 * self-defeating: two visitors at once means two simultaneous vision calls,
 * both slow down, and under load both fail — which is worse for both than one
 * of them waiting thirty seconds. Measured during development, single-user
 * runs already ranged 8s to 89s and one passed two minutes.
 *
 * So work is serialised and the wait is made visible. A queue position is a
 * true number, which is the only kind this sheet prints.
 *
 * State lives in this process. That is a deliberate limit, not an oversight:
 * one small instance is what this is deployed on, and a shared store would add
 * a dependency to solve a problem it does not have. Scaling past one instance
 * means replacing this file, and the README says so.
 */

const CONCURRENCY = Math.max(1, Number(process.env.EXAMINE_CONCURRENCY ?? 1));
const MAX_QUEUED = Math.max(1, Number(process.env.EXAMINE_MAX_QUEUE ?? 12));

/** How long a finished job stays readable, so a slow client can still collect it. */
const KEEP_FINISHED_MS = 10 * 60 * 1000;
/** A job nobody ever polls is abandoned; do not keep its result forever. */
const SWEEP_EVERY_MS = 60 * 1000;

export type JobState = 'queued' | 'running' | 'done' | 'failed';

export interface Job<T> {
  id: string;
  state: JobState;
  enqueuedAt: number;
  startedAt?: number;
  finishedAt?: number;
  result?: T;
  error?: { problem: string; recovery: string };
}

export class QueueFullError extends Error {
  constructor(readonly waiting: number) {
    super(`${waiting} examinations are already waiting`);
  }
}

const jobs = new Map<string, Job<unknown>>();
const pending: string[] = [];
const work = new Map<string, () => Promise<unknown>>();
let running = 0;
let sweeper: ReturnType<typeof setInterval> | undefined;

function sweep() {
  const now = Date.now();
  for (const [id, job] of jobs) {
    if (job.finishedAt && now - job.finishedAt > KEEP_FINISHED_MS) {
      jobs.delete(id);
      work.delete(id);
    }
  }
  if (jobs.size === 0 && sweeper) {
    clearInterval(sweeper);
    sweeper = undefined;
  }
}

/**
 * Turn whatever was thrown into something a visitor can act on.
 *
 * The pipeline raises errors carrying their own `recovery` line, and those are
 * worth showing verbatim. Everything else gets the generic pair. The previous
 * version cast a `recovery`-carrying object straight to `Error` and read
 * `.message` off it — so anything thrown with a recovery but no message printed
 * the literal word "undefined" to the visitor as the problem. Nothing in the
 * codebase throws that shape today, which is exactly why it would have gone
 * unnoticed until something did.
 */
function readableFailure(error: unknown): { problem: string; recovery: string } {
  const GENERIC = {
    problem: 'The examination stopped on an error the sheet did not expect.',
    recovery: 'Run it again. If it repeats, the server log carries the detail.',
  };
  if (!error || typeof error !== 'object' || !('recovery' in error)) return GENERIC;

  const message = 'message' in error ? String((error as { message: unknown }).message) : '';
  const recovery = String((error as { recovery: unknown }).recovery);
  if (!message.trim() || !recovery.trim()) return GENERIC;
  return { problem: message, recovery };
}

function pump() {
  while (running < CONCURRENCY && pending.length > 0) {
    const id = pending.shift()!;
    const job = jobs.get(id);
    const task = work.get(id);
    if (!job || !task) continue;

    running += 1;
    job.state = 'running';
    job.startedAt = Date.now();

    void task()
      .then((result) => {
        job.state = 'done';
        job.result = result;
      })
      .catch((error: unknown) => {
        job.state = 'failed';
        job.error = readableFailure(error);
      })
      .finally(() => {
        job.finishedAt = Date.now();
        work.delete(id);
        running -= 1;
        pump();
      });
  }
}

export function enqueue<T>(task: () => Promise<T>): Job<T> {
  if (pending.length >= MAX_QUEUED) {
    throw new QueueFullError(pending.length);
  }
  const id = crypto.randomUUID();
  const job: Job<T> = { id, state: 'queued', enqueuedAt: Date.now() };
  jobs.set(id, job as Job<unknown>);
  work.set(id, task as () => Promise<unknown>);
  pending.push(id);
  sweeper ??= setInterval(sweep, SWEEP_EVERY_MS).unref?.() ?? setInterval(sweep, SWEEP_EVERY_MS);
  pump();
  return job;
}

/** How many examinations are ahead of this one. 0 once it is running. */
export function positionOf(id: string): number {
  const index = pending.indexOf(id);
  return index < 0 ? 0 : index + 1;
}

export function lookup<T>(id: string): Job<T> | undefined {
  return jobs.get(id) as Job<T> | undefined;
}

export function depth(): { waiting: number; running: number; concurrency: number } {
  return { waiting: pending.length, running, concurrency: CONCURRENCY };
}
