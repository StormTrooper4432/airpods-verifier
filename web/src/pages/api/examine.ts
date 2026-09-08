/**
 * Submit an examination, then poll it.
 *
 * The sheet's own form posts to `/` and blocks until the record is ready,
 * which is fine on a laptop and wrong behind a proxy: a queued run can take
 * minutes and the connection is cut long before it finishes. So the scripted
 * path submits here, gets an id back immediately, and polls — which also gives
 * the progress panel a real queue position to print instead of a guess.
 *
 * The unscripted path still works: `/` keeps its blocking POST, and both go
 * through the same queue, so neither can stampede the vision model.
 */
import type { APIRoute } from 'astro';

import { examine, shotsFrom, validateShots, ExamineError } from '../../lib/examine';
import { depth, enqueue, lookup, positionOf, QueueFullError } from '../../lib/queue';
import { budget, callerAddress, claim, refund } from '../../lib/limits';
import { nextAttempt, record, sessionOf } from '../../lib/record';
import { canReachVerdict, reachableWeight, RULE_BY_PARAMETER } from '../../data/sheet';
import type { PanelId } from '../../data/sheet';
import type { Examination } from '../../lib/examine';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });

export const POST: APIRoute = async ({ request, clientAddress }) => {
  const address = callerAddress(request, clientAddress);
  const session = sessionOf(request);
  const attempt = nextAttempt(session);
  const allowance = claim(address);
  if (!allowance.allowed) {
    record({ session, attempt, outcome: 'rate-limited', offered: [] });
    return new Response(
      JSON.stringify({ problem: allowance.problem, recovery: allowance.recovery }),
      {
        status: 429,
        headers: {
          'content-type': 'application/json',
          'cache-control': 'no-store',
          ...(allowance.retryAfter ? { 'retry-after': String(allowance.retryAfter) } : {}),
        },
      },
    );
  }

  let shots;
  try {
    shots = shotsFrom(await request.formData());
  } catch {
    refund(address);
    record({ session, attempt, outcome: 'rejected', offered: [] });
    return json(
      {
        problem: 'The photographs could not be read from that request.',
        recovery: 'Add them again and run the examination.',
      },
      400,
    );
  }

  try {
    // Validate before queueing: a visitor should learn their file is the wrong
    // type now, not after waiting behind three other examinations.
    validateShots(shots);
    const offered = [...new Set(shots.map((shot) => shot.panel))] as PanelId[];
    const forecast = reachableWeight(offered);
    const warned = !canReachVerdict(offered);
    const startedAt = Date.now();

    const job = enqueue<Examination>(async () => {
      try {
        const examination = await examine(shots);
        const ledger = examination.report.evidence_ledger;
        record({
          session,
          attempt,
          outcome: 'examined',
          offered,
          read: examination.panels,
          forecast,
          warned,
          reached: ledger
            .filter((item) => item.status !== 'UNKNOWN')
            .reduce((total, item) => total + (RULE_BY_PARAMETER[item.parameter]?.weight ?? 0), 0),
          verdict: examination.report.summary_verdict,
          score: examination.report.confidence_score,
          unresolved: ledger.filter((i) => i.status === 'UNKNOWN').map((i) => i.parameter),
          seconds: Math.round((Date.now() - startedAt) / 1000),
        });
        return examination;
      } catch (error) {
        record({
          session,
          attempt,
          outcome: 'failed',
          offered,
          forecast,
          warned,
          seconds: Math.round((Date.now() - startedAt) / 1000),
        });
        throw error;
      }
    });
    return json({ id: job.id, state: job.state, position: positionOf(job.id), ...depth() });
  } catch (error) {
    // Nothing was examined, so nothing should be charged for.
    refund(address);
    record({
      session,
      attempt,
      outcome: error instanceof QueueFullError ? 'queue-full' : 'rejected',
      offered: [],
    });
    if (error instanceof QueueFullError) {
      return json(
        {
          problem: `${error.waiting} examinations are already waiting.`,
          recovery: 'The free tier reads one set at a time. Try again in a few minutes.',
        },
        503,
      );
    }
    if (error instanceof ExamineError) {
      return json({ problem: error.message, recovery: error.recovery }, 400);
    }
    throw error;
  }
};

export const GET: APIRoute = async ({ url }) => {
  const id = url.searchParams.get('job');
  if (!id) return json({ ...depth(), budget: budget() });

  const job = lookup<Examination>(id);
  if (!job) {
    return json(
      {
        problem: 'That examination is no longer on the server.',
        recovery: 'Records are held for ten minutes after they finish. Run it again.',
      },
      404,
    );
  }

  if (job.state === 'failed') return json({ state: 'failed', ...job.error }, 200);
  if (job.state === 'done') {
    return json({
      state: 'done',
      report: job.result?.report,
      panels: job.result?.panels,
      variant: job.result?.variant,
      seconds: job.finishedAt && job.startedAt
        ? Math.round((job.finishedAt - job.startedAt) / 1000)
        : null,
    });
  }
  return json({ state: job.state, position: positionOf(id), ...depth() });
};
