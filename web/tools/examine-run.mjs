// Drive a real examination through the browser: verifies the preview path,
// the busy state, and captures the struck record.
import puppeteer from 'puppeteer-core';
import path from 'node:path';
import fs from 'node:fs';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const OUT = path.resolve('../.impeccable/review');
const dir = path.resolve(process.argv[2]);
const files = fs.readdirSync(dir).filter((f) => /\.(png|jpe?g|webp)$/i.test(f))
  .map((f) => path.join(dir, f));

const PANELS = ['box_front', 'box_bottom_label', 'case_exterior', 'earbud'];

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: 'new',
  args: ['--hide-scrollbars', '--disable-gpu', '--no-sandbox'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
await page.goto('http://127.0.0.1:4321/', { waitUntil: 'networkidle0' });

for (const [i, panel] of PANELS.entries()) {
  if (!files[i]) continue;
  const input = await page.$(`#shot-${panel}`);
  await input.uploadFile(files[i]);
}
await new Promise((r) => setTimeout(r, 400));
const previews = await page.$$eval('.panel-shot', (els) => els.length);
const loaded = await page.$$eval('.panel-hit.is-loaded', (els) => els.length);
console.log(`previews rendered: ${previews}, panels marked loaded: ${loaded}`);
await page.screenshot({ path: path.join(OUT, 'examination-pending.png') });

const started = Date.now();
await page.click('.run');
// The sheet swaps in place now; wait for the grade to stop being blank.
await page.waitForFunction(
  () => !document.querySelector('.grade')?.className.includes('grade-blank'),
  { timeout: 180000, polling: 500 },
);
console.log(`examination returned in ${Math.round((Date.now() - started) / 1000)}s`);

await page.evaluate(() => document.fonts.ready);
await new Promise((r) => setTimeout(r, 1200));
const verdict = await page.$eval('.grade-word', (el) => el.textContent.trim());
const kept = await page.$$eval('.panel-shot', (els) => els.length);
const stillPicked = await page.$$eval('.panel-file', (els) =>
  els.filter((e) => e.files && e.files.length).length);
console.log(`previews kept after examining: ${kept}, files still attached: ${stillPicked}`);
const struck = await page.$$eval('.row-fail .row-param', (els) => els.map((e) => e.textContent));
const steps = await page.$$eval('.steps li', (els) => els.map((e) => e.textContent.trim()));
console.log('verdict:', verdict);
console.log('struck:', struck);
steps.forEach((s) => console.log('  step:', s.replace(/\s+/g, ' ').slice(0, 100)));

await page.screenshot({ path: path.join(OUT, 'examined-desktop.png') });
await page.screenshot({ path: path.join(OUT, 'examined-desktop-full.png'), fullPage: true });
await page.setViewport({ width: 390, height: 844, isMobile: true, deviceScaleFactor: 2 });
await new Promise((r) => setTimeout(r, 500));
await page.screenshot({ path: path.join(OUT, 'examined-mobile.png') });
await browser.close();
