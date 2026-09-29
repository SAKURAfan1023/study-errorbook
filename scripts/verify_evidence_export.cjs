/* Browser QA for scripts/demo.py's labelled synthetic evidence links; never uploads. */
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
(async () => {
  const folder = process.argv[2];
  if (!folder) throw new Error('Pass the trusted synthetic demo export folder');
  const output = path.resolve('tmp/pdfs/evidence-qa');
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  try {
    const context = await browser.newContext({viewport: {width: 1100, height: 1000}});
    let remoteRequests = 0;
    await context.route(/^https?:/, route => { remoteRequests++; return route.abort(); });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(path.join(folder, 'errorbook.html')).href);
    await page.waitForFunction(() => document.documentElement.dataset.mathReady === 'true');
    await page.evaluate(() => document.fonts.ready);
    const state = await page.evaluate(() => ({
      ids: [...document.querySelectorAll('figure[id]')].map(node => node.id),
      links: [...document.querySelectorAll('a[href^="#"]')].map(node => node.hash.slice(1)),
      imagesLoaded: [...document.images].every(image => image.complete && image.naturalWidth > 0),
      text: document.body.innerText,
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    if (state.ids.length !== 3 || new Set(state.ids).size !== 3 || !state.imagesLoaded || state.overflow) throw new Error('Evidence layout failed');
    if (!state.links.every(id => state.ids.includes(id))) throw new Error('Broken evidence anchor');
    if (!state.text.includes('卷面批改（录入）：错误') || !state.text.includes('Agent判断：错误') || !state.text.includes('书写人待确认')) throw new Error('Source distinctions missing');
    await page.locator('.annotations a').click();
    const clicked = await page.evaluate(() => {
      const figure = document.querySelector(location.hash);
      const rect = figure.getBoundingClientRect();
      return {hash: location.hash, visible: rect.bottom > 0 && rect.top < innerHeight};
    });
    if (clicked.hash !== '#evidence-1-2' || !clicked.visible) throw new Error('Annotation did not navigate to its own evidence');
    await page.screenshot({path: path.join(output, 'annotation-click.png')});
    await page.evaluate(() => { history.replaceState(null, '', location.pathname); scrollTo(0, 0); });
    await page.screenshot({path: path.join(output, 'notebook.png'), fullPage: true});
    await page.pdf({path: path.join(output, 'notebook.pdf'), preferCSSPageSize: true, printBackground: true});
    if (remoteRequests || errors.length) throw new Error(JSON.stringify({remoteRequests, errors}));
    const report = {folder, browser: browser.version(), ...state, text: undefined, clicked, remoteRequests, errors};
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
