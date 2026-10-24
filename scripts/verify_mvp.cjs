/* Offline browser acceptance; use an already-installed Playwright via NODE_PATH. */
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
(async () => {
  const [folder, output, extracted] = process.argv.slice(2);
  if (!folder || !output || !extracted) throw new Error('Pass export folder, new screenshot folder and extracted student ZIP folder');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const report = {browser:browser.version(), pages:{}, externalRequests:0, errors:[]};
  try {
    const context = await browser.newContext();
    await context.route(/^https?:/, route => {report.externalRequests++; return route.abort();});
    for (const name of ['errorbook', 'practice', 'answers', 'feedback']) {
      const page = await context.newPage();
      page.on('pageerror', e => report.errors.push(e.message));
      page.on('requestfailed', r => report.errors.push(r.url()));
      report.pages[name] = {};
      for (const [mode, width, height] of [['desktop',1200,1000], ['narrow',390,844], ['print',1000,1200]]) {
        await page.setViewportSize({width,height});
        await page.emulateMedia({media: mode === 'print' ? 'print' : 'screen'});
        await page.goto(pathToFileURL(path.join(folder, name+'.html')).href);
        await page.waitForFunction(() => document.documentElement.dataset.mathReady === 'true');
        await page.evaluate(() => document.fonts.ready);
        const metrics = await page.evaluate(() => ({overflow:document.documentElement.scrollWidth>innerWidth, formulas:document.querySelectorAll('.katex').length, notice:!document.querySelector('[data-math-notice]').hidden, images:[...document.images].every(i=>i.complete&&i.naturalWidth>0), text:document.body.innerText}));
        if (metrics.overflow || metrics.notice || !metrics.images) throw new Error(name+' '+mode+' '+JSON.stringify(metrics));
        if (name === 'practice' && ['3x=9','Agent判断','分配律漏乘常数项','PRIVATE-','反馈与依据'].some(s=>metrics.text.includes(s))) throw new Error('Learner answer/feedback leak');
        await page.screenshot({path:path.join(output,name+'-'+mode+'.png'),fullPage:true});
        if (mode === 'print') await page.pdf({path:path.join(output,name+'-print.pdf'),preferCSSPageSize:true,printBackground:true});
        report.pages[name][mode] = {...metrics, text:undefined};
      }
      await page.close();
    }
    const learner = await context.newPage();
    await learner.setViewportSize({width:390,height:844});
    await learner.goto(pathToFileURL(path.join(extracted,'practice.html')).href);
    await learner.waitForFunction(() => document.documentElement.dataset.mathReady === 'true');
    await learner.evaluate(() => document.fonts.ready);
    report.extractedStudentZip = await learner.evaluate(() => ({formulas:document.querySelectorAll('.katex').length, overflow:document.documentElement.scrollWidth>innerWidth, notice:!document.querySelector('[data-math-notice]').hidden, links:[...document.querySelectorAll('a')].map(a=>a.getAttribute('href'))}));
    if (!report.extractedStudentZip.formulas || report.extractedStudentZip.overflow || report.extractedStudentZip.notice || report.externalRequests || report.errors.length) throw new Error(JSON.stringify(report));
    await learner.screenshot({path:path.join(output,'student-zip-offline.png'),fullPage:true});
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode=1;});
