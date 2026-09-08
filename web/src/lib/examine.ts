/**
 * Runs the real pipeline. The web layer never decides anything: it hands
 * photographs to `main.py` and prints what the rules engine returns.
 */
import { spawn } from 'node:child_process';
import { mkdtemp, rm, writeFile, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { PanelId, Report } from '../data/sheet';
import { redactReport } from './redact';

export class ExamineError extends Error {
  constructor(
    message: string,
    readonly recovery: string,
  ) {
    super(message);
  }
}

/**
 * Find the pipeline. `import.meta.url` is useless here: the bundler moves this
 * module into dist/server/chunks/, so a path relative to it points at the wrong
 * tree in a built server and the right one in dev. Walk up from the working
 * directory looking for the pipeline itself instead, and let the environment
 * override when the two are deployed apart.
 */
function findRoot(): string {
  const holdsPipeline = (dir: string) =>
    existsSync(path.join(dir, 'main.py')) && existsSync(path.join(dir, 'rules_engine.py'));

  if (process.env.VERIFIER_ROOT) {
    const named = path.resolve(process.env.VERIFIER_ROOT);
    if (holdsPipeline(named)) return named;
    throw new ExamineError(
      'The verifier is not where this server was told to look.',
      `VERIFIER_ROOT is ${named}, which holds no main.py. Point it at the ` +
        'pipeline checkout and restart.',
    );
  }

  const seen: string[] = [];
  let dir = process.cwd();
  for (let up = 0; up < 6; up += 1) {
    seen.push(dir);
    if (holdsPipeline(dir)) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  // A deployment that shipped the web app without the pipeline beside it. The
  // buyer gets a plain notice rather than a generic failure, and the operator
  // gets the fix.
  throw new ExamineError(
    'The verifier is not installed beside this server.',
    `main.py was not found above ${process.cwd()}. Set VERIFIER_ROOT to the ` +
      'pipeline checkout and restart.',
  );
}

let cachedRoot: string | null = null;
function root(): string {
  cachedRoot ??= findRoot();
  return cachedRoot;
}

function python(): string {
  if (process.env.VERIFIER_PYTHON) return process.env.VERIFIER_PYTHON;
  const venv = path.join(root(), '.venv', 'bin', 'python');
  return existsSync(venv) ? venv : 'python3';
}

export const MAX_FILES = 10;
export const MAX_BYTES = 8 * 1024 * 1024;
/**
 * Measured on the free tier: 8s to 89s for four photographs, 53s for two, and
 * one run past 120s. The old two-minute ceiling cut off examinations that were
 * still working, and the number of photographs was never the cause — the wait
 * is the vision model's queue, not the upload.
 */
const TIMEOUT_MS = 300_000;

const EXT: Record<string, string> = {
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/webp': '.webp',
  'image/heic': '.heic',
  'image/heif': '.heic',
};

export interface Examination {
  report: Report;
  panels: PanelId[];
  variant: string | null;
}

function run(args: string[]): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(python(), args, {
      cwd: root(),
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(
        new ExamineError(
          'The vision model did not answer within five minutes.',
          'This is free-tier congestion, not your photographs. Wait a minute and ' +
            'run it again; the ones you added are still attached.',
        ),
      );
    }, TIMEOUT_MS);
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    child.on('error', (err) =>
      reject(
        new ExamineError(
          `The verifier could not be started (${err.message}).`,
          `Check that ${python()} exists and that the project's requirements are installed.`,
        ),
      ),
    );
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({ code: code ?? -1, stdout, stderr });
    });
  });
}

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
  for (const { file } of shots) {
    if (!EXT[file.type]) {
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
  }

}

export async function examine(shots: Shot[]): Promise<Examination> {
  validateShots(shots);

  const dir = await mkdtemp(path.join(tmpdir(), 'airpods-exam-'));
  try {
    const images: string[] = [];
    for (const [i, { panel, file }] of shots.entries()) {
      const name = `${String(i).padStart(2, '0')}-${panel}${EXT[file.type]}`;
      const full = path.join(dir, name);
      await writeFile(full, Buffer.from(await file.arrayBuffer()));
      images.push(full);
    }

    const extractionPath = path.join(dir, 'extraction.json');
    const { code, stdout, stderr } = await run([
      'main.py',
      ...images,
      '--json',
      '--lite-only',
      '--save-extraction',
      extractionPath,
    ]);

    if (!stdout.trim()) {
      const missingKey = /GEMINI_API_KEY|API key/i.test(stderr);
      const tail = stderr.trim().split('\n').filter(Boolean).slice(-1)[0];
      if (!missingKey) console.error('[examine] no report, status', code, '\n', stderr);
      throw new ExamineError(
        missingKey
          ? 'The vision backend is not configured on this server.'
          : `The verifier exited without a report (status ${code}).`,
        missingKey
          ? 'Set GEMINI_API_KEY in the server environment and restart.'
          : (tail ?? 'Check the server log.'),
      );
    }

    // Masked here, at the one boundary every report crosses.
    const report = redactReport(JSON.parse(stdout) as Report);
    let panels: PanelId[] = [];
    let variant: string | null = null;
    try {
      const ex = JSON.parse(await readFile(extractionPath, 'utf-8'));
      panels = ex.visible_panels ?? [];
      variant = ex.claimed_model_variant ?? null;
    } catch {
      // The plot degrades to the panels the visitor said they shot.
      panels = [...new Set(shots.map((s) => s.panel))];
    }
    return { report, panels, variant };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/** Reads the multipart body the sheet posts. */
export function shotsFrom(form: FormData): Shot[] {
  const shots: Shot[] = [];
  for (const [key, value] of form.entries()) {
    if (!key.startsWith('panel_')) continue;
    if (!(value instanceof File) || value.size === 0) continue;
    shots.push({ panel: key.slice('panel_'.length) as PanelId, file: value });
  }
  return shots;
}
