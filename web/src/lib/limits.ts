/**
 * Who is allowed to spend the day's vision quota.
 *
 * A public URL carrying somebody's personal free-tier key is an open tap. The
 * queue already stops examinations running at once, but serialising abuse is
 * still abuse: one determined caller can drain the daily allowance and the
 * next real buyer gets nothing.
 *
 * Two limits, because they fail differently. The per-address window stops one
 * caller monopolising the service. The daily budget is the backstop that
 * protects the quota itself no matter how many addresses arrive, and it is the
 * one that actually bounds the bill.
 *
 * Both live in this process, like the queue. One small instance is what this
 * deploys onto; scaling past one means moving all three to a shared store, and
 * the README says so.
 */

/** A real buyer examines one unit, maybe re-shoots it twice. */
const PER_ADDRESS_HOURLY = Math.max(1, Number(process.env.EXAMINE_PER_IP_HOURLY ?? 6));
const PER_ADDRESS_DAILY = Math.max(1, Number(process.env.EXAMINE_PER_IP_DAILY ?? 20));

/**
 * Total examinations a day, across everybody. Deliberately well under the
 * provider's free-tier allowance: running out mid-afternoon and telling
 * visitors nothing is worse than turning some away early with a reason.
 */
const DAILY_BUDGET = Math.max(1, Number(process.env.EXAMINE_DAILY_BUDGET ?? 300));

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

export interface Allowance {
  allowed: boolean;
  problem?: string;
  recovery?: string;
  /** Seconds until this caller could try again, when that is knowable. */
  retryAfter?: number;
}

const seen = new Map<string, number[]>();
let budgetSpent = 0;
let budgetWindowStartedAt = Date.now();

/**
 * The caller's address behind Cloud Run's proxy.
 *
 * `X-Forwarded-For` is client, then each proxy. The first entry is the one
 * that matters, and it is only as trustworthy as the proxy in front — which is
 * why the daily budget exists as well: a spoofed header defeats the per-address
 * window but cannot defeat a total.
 */
export function callerAddress(request: Request, fallback?: string): string {
  const forwarded = request.headers.get('x-forwarded-for');
  if (forwarded) {
    const first = forwarded.split(',')[0]?.trim();
    if (first) return first;
  }
  return request.headers.get('x-real-ip')?.trim() || fallback || 'unknown';
}

function prune(now: number) {
  for (const [address, times] of seen) {
    const recent = times.filter((at) => now - at < DAY_MS);
    if (recent.length) seen.set(address, recent);
    else seen.delete(address);
  }
}

/** Check and, if allowed, count it. Call once per accepted examination. */
export function claim(address: string, now = Date.now()): Allowance {
  if (now - budgetWindowStartedAt >= DAY_MS) {
    budgetSpent = 0;
    budgetWindowStartedAt = now;
    prune(now);
  }

  if (budgetSpent >= DAILY_BUDGET) {
    const resetsIn = Math.ceil((budgetWindowStartedAt + DAY_MS - now) / 1000);
    return {
      allowed: false,
      problem: "This service's examinations for today are used up.",
      recovery:
        'It runs on a free tier with a daily allowance, shared by everyone using it. ' +
        'It resets within the day. The command-line tool in the project has no such limit.',
      retryAfter: resetsIn,
    };
  }

  const times = seen.get(address) ?? [];
  const lastHour = times.filter((at) => now - at < HOUR_MS);
  const lastDay = times.filter((at) => now - at < DAY_MS);

  if (lastHour.length >= PER_ADDRESS_HOURLY) {
    const oldest = Math.min(...lastHour);
    return {
      allowed: false,
      problem: `That is ${PER_ADDRESS_HOURLY} examinations from here within the hour.`,
      recovery:
        'The allowance is shared with everyone else using this, so it is rationed rather ' +
        'than first-come. Wait a little and run it again; your photographs stay attached.',
      retryAfter: Math.ceil((oldest + HOUR_MS - now) / 1000),
    };
  }

  if (lastDay.length >= PER_ADDRESS_DAILY) {
    const oldest = Math.min(...lastDay);
    return {
      allowed: false,
      problem: `That is ${PER_ADDRESS_DAILY} examinations from here today.`,
      recovery:
        'The daily allowance is shared. If you are checking this many units, run the ' +
        'command-line tool with your own API key instead — it has no limit.',
      retryAfter: Math.ceil((oldest + DAY_MS - now) / 1000),
    };
  }

  times.push(now);
  seen.set(address, times);
  budgetSpent += 1;
  return { allowed: true };
}

/** Give a claim back when the examination never actually ran. */
export function refund(address: string) {
  const times = seen.get(address);
  if (times?.length) times.pop();
  if (budgetSpent > 0) budgetSpent -= 1;
}

export function budget(): { spent: number; of: number; addresses: number } {
  return { spent: budgetSpent, of: DAILY_BUDGET, addresses: seen.size };
}

/** Test seam: forget everything. */
export function reset() {
  seen.clear();
  budgetSpent = 0;
  budgetWindowStartedAt = Date.now();
}
