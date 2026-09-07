// Drive a real examination through the browser at each shipped viewport.
//
// The examination must run AT the target viewport: puppeteer reloads the page
// when `isMobile` or `deviceScaleFactor` change, so resizing after examining
// silently throws the struck record away and re-issues a GET.
import puppeteer from 'puppeteer-core';
import path from 'node:path';
import fs from 'node:fs';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const OUT = path.resolve('../.impeccable/review');
const dir = path.resolve(process.argv[2]);
const files = fs
  .readdirSync(dir)
  .filter((f) => /\.(png|jpe?g|webp)$/i.test(f))
  .sort()
  .map((f) => path.join(dir, f));

const PANELS = ['box_front', 'box_bottom_label', 'case_exterior', 'earbud'];
const VIEWPORTS = [
  { name: 'examined-desktop', width: 1440, height: 900 },
  { name: 'examined-mobile', width: 390, height: 844, isMobile: true, deviceScaleFactor: 2 },
];

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--hide-scrollbars', '--disable-gpu', '--no-sandbox'],
});

for (const vp of VIEWPORTS) {
  const page = await browser.newPage();
  await page.setViewport(vp);
  await page.goto('http://127.0.0.1:4321/', { waitUntil: 'networkidle0' });
  await page.evaluate(() => document.fonts.ready);

  for (const [i, panel] of PANELS.entries()) {
    if (!files[i]) continue;
    const input = await page.$(`#shot-${panel}`);
    await input.uploadFile(files[i]);
  }
  await new Promise((r) => setTimeout(r, 400));
  const picked = await page.$$eval('.panel-hit.is-loaded', (els) => els.length);

  const started = Date.now();
  await page.click('.run');
  await page.waitForFunction(
    () => !document.querySelector('.grade')?.className.includes('grade-blank'),
    { timeout: 180000, polling: 400 },
  );
  const seconds = Math.round((Date.now() - started) / 1000);

  await page.evaluate(() => document.fonts.ready);
  await new Promise((r) => setTimeout(r, 1400));

  const state = await page.evaluate(() => ({
    verdict: document.querySelector('.grade-word')?.textContent.trim(),
    coverage: document.querySelector('.readout dd')?.textContent.replace(/\s+/g, ' ').trim(),
    struck: [...document.querySelectorAll('.col-findings .row-fail .row-param')].map((e) =>
      e.textContent.trim(),
    ),
    marks: document.querySelectorAll('.plot-net g.mark').length,
    shots: document.querySelectorAll('.panel-shot').length,
    attached: [...document.querySelectorAll('.panel-file')].filter((e) => e.files?.length).length,
    steps: [...document.querySelectorAll('#settle li')].map((e) =>
      e.textContent.replace(/\s+/g, ' ').trim().slice(0, 90),
    ),
  }));

  console.log(
    `${vp.name}  ${vp.width}px  picked ${picked}  examined in ${seconds}s\n` +
      `   verdict: ${state.verdict}\n` +
      `   coverage: ${state.coverage}\n` +
      `   struck: ${state.struck.join(', ') || '(none)'}\n` +
      `   marks on plot: ${state.marks}   photos kept: ${state.shots}   files attached: ${state.attached}`,
  );
  state.steps.forEach((s) => console.log(`   step: ${s}`));

  // The struck record's own first viewport, from the top of the document.
  await page.evaluate(() => window.scrollTo(0, 0));
  await new Promise((r) => setTimeout(r, 350));
  await page.screenshot({ path: path.join(OUT, `${vp.name}.png`) });
  await page.screenshot({ path: path.join(OUT, `${vp.name}-full.png`), fullPage: true });
  await page.close();
}

await browser.close();
