/**
 * The upload gate: what this accepts, decided before anything touches disk.
 *
 * Kept apart from examine.ts, which does I/O and shells out to the pipeline,
 * so node can load and exercise it directly — the same way the rate limiter
 * and the origin check are tested. A gate checked through a re-implementation
 * of itself is not the gate that ships.
 */
import type { PanelId } from '../data/sheet';

/**
 * An error carrying the recovery line the visitor is shown.
 *
 * Written with a plain field rather than a constructor parameter property so
 * node's strip-only TypeScript can load this module directly. That is not
 * cosmetic: it is what lets tools/examine.test.mjs exercise the shipped upload
 * gate instead of a re-implementation of it, the same way the rate limiter and
 * the origin check are tested.
 */
export class ExamineError extends Error {
  readonly recovery: string;

  constructor(message: string, recovery: string) {
    super(message);
    this.recovery = recovery;
  }
}

export const MAX_FILES = 10;
export const MAX_BYTES = 8 * 1024 * 1024;
/**
 * Ceiling on the whole set, not just each file.
 *
 * Ten files at the per-file limit is 80 MB, and the per-file limit was the
 * only guard. What actually costs memory is decompression, not transfer: a
 * 12-megapixel photograph is about 36 MB of raw RGB once Pillow opens it, and
 * this deploys onto an instance with 512 MB and a tenth of a CPU. Bounding
 * the set turns a possible out-of-memory kill — which reaches the visitor as
 * a dead connection and no explanation — into a sentence telling them what to
 * do. The guided set is three photographs; this fits ten ordinary ones.
 */
export const MAX_TOTAL_BYTES = 32 * 1024 * 1024;
/**
 * Measured on the free tier: 8s to 89s for four photographs, 53s for two, and
 * one run past 120s. The old two-minute ceiling cut off examinations that were
 * still working, and the number of photographs was never the cause — the wait
 * is the vision model's queue, not the upload.
 */

const EXT: Record<string, string> = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/webp': '.webp',
  'image/heic': '.heic',
  'image/heif': '.heic',
};

/**
 * The suffix to save this upload under, or null if it is not a photograph.
 *
 * Browsers do not always know what a HEIC is. Android in particular hands over
 * a file with an empty `type`, and matching on MIME alone rejected a perfectly
 * good photograph with "unknown type" — the one format an iPhone shoots by
 * default. So fall back to the filename when the browser has no opinion.
 *
 * Trusting the name costs nothing here: it only picks a temp-file suffix. What
 * the file actually is gets decided downstream by Pillow, which reads the
 * bytes and raises if they are not an image.
 */
export function extensionFor(file: File): string | null {
  const byType = EXT[file.type];
  if (byType) return byType;
  if (file.type) return null; // the browser was sure, and it was sure of something else
  const match = /\.([a-z0-9]+)$/i.exec(file.name || '');
  const byName = match ? EXT_BY_SUFFIX[match[1]!.toLowerCase()] : undefined;
  return byName ?? null;
}

const EXT_BY_SUFFIX: Record<string, string> = {
  jpg: '.jpg', jpeg: '.jpg', png: '.png', webp: '.webp', heic: '.heic', heif: '.heic',
};

/** Photographs the visitor supplied, in the order the plot lists them. */
export interface Shot {
  panel: PanelId;
  file: File;
}

/**
 * Reject a bad set before it joins the queue. Learning that a file is the
 * wrong type after waiting behind three other examinations is a worse answer
 * than learning it immediately, and costs the queue nothing.
 */
export function validateShots(shots: Shot[]): void {
  if (shots.length === 0) {
    throw new ExamineError(
      'No photographs were supplied.',
      'Tap a panel on the plot and add a photograph of that surface.',
    );
  }
  if (shots.length > MAX_FILES) {
    throw new ExamineError(
      `More than ${MAX_FILES} photographs were supplied.`,
      `Send at most ${MAX_FILES}: the panels on the plot are enough.`,
    );
  }
  let total = 0;
  for (const { file } of shots) {
    if (!extensionFor(file)) {
      throw new ExamineError(
        `${file.name || 'A file'} is not a photograph the verifier reads (${file.type || 'unknown type'}).`,
        'Send JPEG, PNG, WebP or HEIC.',
      );
    }
    if (file.size > MAX_BYTES) {
      throw new ExamineError(
        `${file.name || 'A file'} is larger than ${MAX_BYTES / 1024 / 1024} MB.`,
        'Photographs straight from a phone camera are within the limit; reduce the file and try again.',
      );
    }
    total += file.size;
  }

  if (total > MAX_TOTAL_BYTES) {
    throw new ExamineError(
      `Those photographs come to ${(total / 1024 / 1024).toFixed(0)} MB together, over the ` +
        `${MAX_TOTAL_BYTES / 1024 / 1024} MB this can hold at once.`,
      'Send fewer at a time. The underside label on its own is enough for a verdict, and ' +
        'adding the case lid and one side edge covers every check there is.',
    );
  }
}
