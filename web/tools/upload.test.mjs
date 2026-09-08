/**
 * The upload gate.
 *
 * Everything here guards a photograph that a real person took on a real phone
 * and expects an answer about. A wrong rejection here is indistinguishable
 * from the tool being broken, because from their side it is.
 */
import { validateShots, extensionFor, MAX_FILES, MAX_BYTES, MAX_TOTAL_BYTES }
  from '../src/lib/upload.ts';

let pass = 0, fail = 0;
const ok = (cond, label) => {
  if (cond) { pass += 1; console.log(`  ok   ${label}`); }
  else { fail += 1; console.log(`  FAIL ${label}`); }
};

/** A File of a given size without allocating it. */
const shot = (panel, { type = 'image/jpeg', name = 'a.jpg', size = 1024 } = {}) => ({
  panel,
  file: { type, name, size },
});

const throws = (shots) => {
  try { validateShots(shots); return null; }
  catch (error) { return error; }
};

console.log('\nextensionFor');
ok(extensionFor({ type: 'image/jpeg', name: 'x.jpg' }) === '.jpg', 'jpeg by MIME');
ok(extensionFor({ type: 'image/heic', name: 'x.heic' }) === '.heic', 'heic by MIME');
ok(extensionFor({ type: 'image/heif', name: 'x.heif' }) === '.heic', 'heif maps to .heic');
// The case that rejected a valid iPhone photograph: browser has no opinion.
ok(extensionFor({ type: '', name: 'IMG_0042.HEIC' }) === '.heic', 'empty type falls back to name');
ok(extensionFor({ type: '', name: 'IMG_0042.jpeg' }) === '.jpg', 'uppercase and jpeg alias');
ok(extensionFor({ type: '', name: 'notes' }) === null, 'no type and no suffix is refused');
ok(extensionFor({ type: '', name: 'archive.zip' }) === null, 'a non-image suffix is refused');
// A browser that IS sure it is a PDF must not be overridden by a lying name.
ok(extensionFor({ type: 'application/pdf', name: 'sneaky.jpg' }) === null,
   'a stated non-image type wins over the filename');

console.log('\nvalidateShots');
ok(throws([])?.message.includes('No photographs'), 'an empty set is refused');
ok(throws([shot('box_front')]) === null, 'one ordinary photograph passes');

const tooMany = Array.from({ length: MAX_FILES + 1 }, () => shot('box_front'));
ok(throws(tooMany)?.message.includes(String(MAX_FILES)), 'over the file count is refused');

ok(throws([shot('box_front', { size: MAX_BYTES + 1 })])?.message.includes('larger than'),
   'a single oversized file is refused');
ok(throws([shot('box_front', { type: 'application/pdf', name: 'x.pdf' })])
     ?.message.includes('not a photograph'), 'a non-photograph is refused');
ok(throws([shot('box_front', { type: '', name: 'IMG_1.HEIC' })]) === null,
   'a typeless HEIC from a phone is accepted');

console.log('\ntotal size');
// Each file inside the per-file limit, the set over the total. This was the
// gap: ten files at 8 MB passed every check and came to 80 MB.
const heavy = Array.from({ length: 8 }, () => shot('box_front', { size: 5 * 1024 * 1024 }));
const err = throws(heavy);
ok(err !== null, 'a set that is individually legal but collectively too big is refused');
ok(err?.message.includes('together'), 'the message says it is the total');
ok(err?.recovery?.includes('underside label'),
   'the recovery names the one photograph that is enough');

const justUnder = Array.from({ length: 4 }, () => shot('box_front', { size: 4 * 1024 * 1024 }));
ok(throws(justUnder) === null, 'a realistic set of four phone photographs passes');
ok(MAX_TOTAL_BYTES < MAX_FILES * MAX_BYTES,
   'the total cap actually binds below the per-file cap times the count');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
