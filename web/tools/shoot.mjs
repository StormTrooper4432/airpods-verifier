// Capture and audit the sheet. Not shipped: a development instrument.
import puppeteer from 'puppeteer-core';
import path from 'node:path';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const URL = process.env.SHEET_URL ?? 'http://127.0.0.1:4321/';
const OUT = path.resolve(process.argv[2] ?? '../.impeccable/review');

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'wide', width: 1280, height: 900 },
  { name: 'mobile', width: 390, height: 844, isMobile: true, deviceScaleFactor: 2 },
];

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--hide-scrollbars', '--disable-gpu', '--no-sandbox'],
});

for (const vp of VIEWPORTS) {
  const page = await browser.newPage();
  await page.setViewport(vp);
  await page.goto(URL, { waitUntil: 'networkidle0' });
  await page.evaluate(() => document.fonts.ready);

  const audit = await page.evaluate(() => {
    const doc = document.documentElement;
    const overflow = doc.scrollWidth - doc.clientWidth;
    const wide = [];
    if (overflow > 0) {
      for (const el of document.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        if (r.right > doc.clientWidth + 1 && r.width > 0) {
          wide.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className?.baseVal ?? el.className ?? '').toString().slice(0, 48),
            right: Math.round(r.right),
            width: Math.round(r.width),
          });
        }
      }
    }
    // Nothing may sit on colour alone, and nothing may be unlabelled.
    const named = (el) => {
      if (el.getAttribute('aria-label')?.trim()) return true;
      if ((el.innerText || '').trim()) return true;
      if (el.querySelector('.sr-only')) return true;
      // A wrapping <label>, or one bound by `for`, names the control.
      const wrap = el.closest('label');
      if (wrap && (wrap.innerText.trim() || wrap.querySelector('.sr-only'))) return true;
      if (el.id) {
        const bound = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (bound && (bound.innerText.trim() || bound.querySelector('.sr-only'))) return true;
      }
      return false;
    };
    const unlabelled = [...document.querySelectorAll('button, a, input')]
      .filter((el) => !named(el))
      .map((el) => el.tagName.toLowerCase() + '.' + (el.className || ''));
    // A mark must never sit on a surface nobody photographed: a tick on a blank
    // panel would claim evidence that does not exist.
    const ghostMarks = [...document.querySelectorAll('g.panel.is-blank')]
      .filter((g) => g.querySelector('g.mark'))
      .map((g) => g.dataset.panel);

    // Text on the paper ground must clear 4.5:1 (3:1 for large text).
    const srgb = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const lum = (rgb) => {
      const [r, g, b] = rgb.map((v) => srgb(v / 255));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const parse = (s) => (s.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
    // The nearest painted ancestor is the backdrop, not always <body>.
    const backdrop = (el) => {
      for (let n = el; n; n = n.parentElement) {
        const c = getComputedStyle(n).backgroundColor;
        const p = parse(c);
        const alpha = (c.match(/[\d.]+/g) ?? [])[3];
        if (p.length === 3 && alpha !== '0') return lum(p);
      }
      return lum(parse(getComputedStyle(document.body).backgroundColor));
    };
    const lowContrast = [];
    for (const el of document.querySelectorAll('p, li, dd, dt, td, th, span, h1, h2, h3, b')) {
      if (!el.firstChild || el.firstChild.nodeType !== 3) continue;
      if (!el.textContent.trim()) continue;
      const cs = getComputedStyle(el);
      const size = parseFloat(cs.fontSize);
      const bold = parseInt(cs.fontWeight, 10) >= 700;
      const large = size >= 24 || (size >= 18.66 && bold);
      const fg = lum(parse(cs.color));
      const bg = backdrop(el);
      const ratio = (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
      if (ratio < (large ? 3 : 4.5)) {
        lowContrast.push({
          text: el.textContent.trim().slice(0, 34),
          cls: (el.className || '').toString().slice(0, 30),
          size: Math.round(size),
          ratio: ratio.toFixed(2),
        });
      }
    }

    return {
      ghostMarks,
      lowContrast: lowContrast.slice(0, 10),
      overflow,
      scrollWidth: doc.scrollWidth,
      clientWidth: doc.clientWidth,
      pageHeight: doc.scrollHeight,
      offenders: wide.slice(0, 12),
      unlabelled: unlabelled.slice(0, 8),
    };
  });

  await page.screenshot({ path: path.join(OUT, `${vp.name}.png`), fullPage: false });
  await page.screenshot({ path: path.join(OUT, `${vp.name}-full.png`), fullPage: true });
  console.log(
    `${vp.name.padEnd(8)} ${vp.width}x${vp.height}  page ${audit.pageHeight}px  ` +
      `overflow ${audit.overflow}px (scroll ${audit.scrollWidth} / client ${audit.clientWidth})`,
  );
  if (audit.offenders.length) {
    for (const o of audit.offenders) {
      console.log(`   past the edge: <${o.tag} class="${o.cls}"> right=${o.right} w=${o.width}`);
    }
  }
  if (audit.unlabelled.length) console.log('   unlabelled controls:', audit.unlabelled);
  if (audit.ghostMarks.length)
    console.log('   MARK ON AN UNPHOTOGRAPHED PANEL:', audit.ghostMarks.join(', '));
  for (const c of audit.lowContrast) {
    console.log(`   contrast ${c.ratio} @${c.size}px  .${c.cls}  "${c.text}"`);
  }
  await page.close();
}

await browser.close();
