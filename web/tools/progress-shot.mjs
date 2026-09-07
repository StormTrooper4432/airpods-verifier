// Capture the examining state mid-flight, then the finished record.
import puppeteer from 'puppeteer-core';
import path from 'node:path';
import fs from 'node:fs';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const OUT = path.resolve('../.impeccable/review');
const dir = path.resolve(process.argv[2]);
const files = fs.readdirSync(dir).filter((f) => /\.(png|jpe?g)$/i.test(f)).sort()
  .map((f) => path.join(dir, f)).slice(0, 2);

const browser = await puppeteer.launch({
  executablePath: CHROME, headless: 'new',
  args: ['--hide-scrollbars', '--disable-gpu', '--no-sandbox'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
await page.goto('http://127.0.0.1:4321/', { waitUntil: 'networkidle0' });
await page.evaluate(() => document.fonts.ready);

for (const [i, panel] of ['box_front', 'box_bottom_label'].entries()) {
  await (await page.$(`#shot-${panel}`)).uploadFile(files[i]);
}
await new Promise((r) => setTimeout(r, 300));
await page.mouse.move(1438, 2);
await page.click('.run');

for (const at of [4, 12, 30]) {
  await new Promise((r) => setTimeout(r, at === 4 ? 4000 : (at === 12 ? 8000 : 18000)));
  const done = await page.evaluate(() => document.getElementById('examining')?.hidden);
  if (done) { console.log(`finished before ${at}s`); break; }
  const state = await page.evaluate(() => ({
    stage: document.getElementById('examining-stage')?.textContent.trim(),
    elapsed: document.getElementById('examining-elapsed')?.textContent,
    lit: [...document.querySelectorAll('.examining-rail span')].length,
  }));
  console.log(`  ${state.elapsed}  "${state.stage}"  (${state.lit} cells)`);
  await page.mouse.move(1438, 2);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(OUT, `examining-${at}s.png`) });
}

await page.waitForFunction(() => document.getElementById('examining')?.hidden === true,
  { timeout: 300000, polling: 500 });
await new Promise((r) => setTimeout(r, 1200));
const after = await page.evaluate(() => ({
  verdict: document.querySelector('.grade-word')?.textContent.trim(),
  restart: !document.getElementById('restart')?.hidden,
  photos: document.querySelectorAll('.panel-shot').length,
}));
console.log('after:', JSON.stringify(after));
await page.mouse.move(1438, 2);
await page.evaluate(() => window.scrollTo(0, 0));
await page.screenshot({ path: path.join(OUT, 'examined-with-restart.png') });
await browser.close();
