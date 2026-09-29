/* Optional developer QA: Node + Playwright + Chrome; exports remain browser-only. */
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

(async () => {
  const folder = process.argv[2];
  if (!folder) throw new Error('Pass a trusted synthetic export folder');
  const output = path.resolve('tmp/pdfs/print-qa');
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const report = {};
  try {
    const context = await browser.newContext({viewport: {width: 1100, height: 1000}});
    let remoteRequests = 0;
    await context.route(/^https?:/, route => { remoteRequests++; return route.abort(); });
    for (const name of ['errorbook', 'practice', 'answers']) {
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(pathToFileURL(path.join(folder, name + '.html')).href);
      await page.waitForFunction(() => document.documentElement.dataset.mathReady === 'true');
      await page.evaluate(() => document.fonts.ready);
      const metrics = await page.evaluate(() => ({
        formulas: document.querySelectorAll('.katex').length,
        warnings: !document.querySelector('[data-math-notice]').hidden,
        overflow: document.documentElement.scrollWidth > innerWidth,
        answerLeak: document.body.innerText.includes('答案专用标记'),
        cards: document.querySelectorAll('.exercise').length,
        text: document.body.innerText
      }));
      if (!metrics.formulas || metrics.warnings || metrics.overflow || errors.length) throw new Error(name + ': ' + JSON.stringify({metrics, errors}));
      if (name === 'practice' && (metrics.answerLeak || metrics.cards !== 6)) throw new Error('Student output contract failed');
      if (name === 'errorbook' && !metrics.text.includes('$5 与 $10')) throw new Error('Currency was rendered as math');
      await page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
      await page.pdf({path: path.join(output, name + '.pdf'), preferCSSPageSize: true, printBackground: true});
      report[name] = {...metrics, text: undefined, errors};
      await page.close();
    }
    // Use the real exported renderer, then inject escaped text for malformed/untrusted formula cases.
    const page = await context.newPage();
    await page.goto(pathToFileURL(path.join(folder, 'practice.html')).href);
    await page.waitForFunction(() => document.documentElement.dataset.mathReady === 'true');
    const hostile = String.raw`\(\includegraphics{https://example.invalid/track}\) \(\badcommand{1}\) <img src=x onerror=alert(1)>`;
    await page.evaluate(value => { document.querySelector('.rich-text').textContent = value; }, hostile);
    await page.addScriptTag({path: path.join(folder, 'assets/export.js')});
    const fallback = await page.evaluate(() => ({
      warning: !document.querySelector('[data-math-notice]').hidden,
      raw: document.querySelector('.rich-text').innerText.includes('badcommand'),
      images: document.querySelectorAll('img').length,
    }));
    if (!fallback.warning || !fallback.raw || fallback.images || remoteRequests) throw new Error('Fallback/security check failed');
    report.fallback = fallback;
    report.remoteRequests = remoteRequests;
    report.browser = browser.version();
    report.exportFolder = folder;
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
