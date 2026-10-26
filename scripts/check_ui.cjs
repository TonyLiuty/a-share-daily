// Offline browser verification. Uses the installed desktop runtime, no downloads.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {fileURLToPath} = require('node:url');
function section(md, heading) {
  const lines=md.split(/\r?\n/), start=lines.indexOf(heading);
  if(start<0)return '';
  const level=heading.match(/^#+/)[0].length;
  let end=start+1;
  while(end<lines.length && !new RegExp('^#{1,'+level+'} ').test(lines[end]))end++;
  return lines.slice(start+1,end).join('\n');
}
function expected(directory) {
  const md=fs.readFileSync(path.join(directory,'report.md'),'utf8');
  return {
    events:section(md,'### 重大事件与市场影响分析').split('\n').filter(x=>/^\|\s*\[[^\]]+\]\([^)]+\)/.test(x)).length,
    stocks:section(md,'## 二、今日观察池').split('\n').filter(x=>/^\|\s*\d+\s*\|\s*\d{6}\s*\|/.test(x)).length,
    indices:section(md,'### 主要指数表现').split('\n').filter(x=>/^\|\s*(上证指数|深证成指|创业板指|沪深300)\s*\|/.test(x)).length,
    charts:(section(md,'## 三、逐只股票详细分析').match(/!\[[^\]]*\]\([^)]+\)/g)||[]).length,
  };
}
(async () => {
  const directory = process.argv[2];
  if (!directory) throw new Error('Usage: node scripts/check_ui.cjs <dated-report-directory>');
  const want=expected(directory);
  const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1050}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(pathToFileURL(path.join(directory,'report.html')).href);
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('.index').count(),want.indices,'index count differs from Markdown');
    assert.equal(await page.locator('.stock-detail').count(),want.stocks,'stock count differs from Markdown');
    assert.equal(await page.locator('#news .event').count(),want.events,'event count differs from Markdown');
    assert.equal(await page.locator('.industry-stock-link').count(),want.stocks,'industry map missing stocks');
    assert.equal(await page.locator('details[open]').count(),0);
    await page.screenshot({path:path.join(directory,'overview-desktop.png')});
    await page.locator('#news').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(directory,'news-desktop.png')});
    if(want.stocks){
    const firstTarget=await page.locator('.stock-link').first().getAttribute('href');
    await page.locator('.stock-link').first().click();
    assert.equal(await page.locator(firstTarget).getAttribute('open'),'');
    const industryTarget=await page.locator('.industry-stock-link').first().getAttribute('href');
    await page.locator('.industry-stock-link').first().click();
    assert.equal(await page.locator(industryTarget).getAttribute('open'),'');
    const firstStockCode = await page.locator('.stock-link').first().textContent();
    await page.locator('#stock-search').fill(firstStockCode.trim());
    assert.equal(await page.locator('.stock-detail:not([hidden])').count(),1);
    await page.locator('#stock-search').fill('不存在的股票');
    assert.equal(await page.locator('.stock-detail:not([hidden])').count(),0);
    await page.locator('#stock-search').fill('');
    }
    const openBefore=await page.locator('details[open]').evaluateAll(els=>els.map(e=>e.id));
    // Print opens all details and restores the browsing state afterwards.
    await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
    await page.locator('#quality').scrollIntoViewIfNeeded();
    await page.locator('img').evaluateAll(imgs => imgs.forEach(img => img.loading='eager'));
    await page.waitForFunction(() => [...document.images].every(img => img.complete));
    assert.equal(await page.locator('img').count(),want.charts);
    assert.equal(await page.locator('img').evaluateAll(imgs=>imgs.filter(x=>!x.naturalWidth).length),0);
    const ids = await page.locator('[id]').evaluateAll(els=>els.map(e=>e.id));
    assert.equal(new Set(ids).size,ids.length,'duplicate anchors');
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    assert.deepEqual(await page.locator('details[open]').evaluateAll(els=>els.map(e=>e.id)),openBefore,'print must restore open state');
    await page.setViewportSize({width:390,height:844});
    await page.goto(pathToFileURL(path.join(directory,'report.html')).href);
    await page.waitForLoadState('networkidle');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),'mobile page overflow');
    await page.screenshot({path:path.join(directory,'overview-mobile.png')});
    const latest=path.resolve(directory,'../..','latest.html');
    let latestDate=null;
    if(fs.existsSync(latest)){
      await page.goto(pathToFileURL(latest).href);
      const mdPath=fileURLToPath(await page.locator('.downloads a').first().evaluate(a=>a.href));
      const latestDir=path.dirname(mdPath);
      assert.equal(path.basename(mdPath),'report.md');
      assert.equal(path.resolve(latestDir,'../..'),path.dirname(latest),'latest target must stay inside output/daily');
      assert.equal(path.basename(path.dirname(latestDir)),'daily');
      latestDate=path.basename(latestDir);
      const latestWant=expected(latestDir);
      assert.equal(await page.locator('.stock-detail').count(),latestWant.stocks);
      assert.equal(await page.locator('#news .event').count(),latestWant.events);
      assert((await page.title()).includes(latestDate),'latest title/download date mismatch');
    }
    assert.equal(errors.length,0,errors.join('\n'));
    console.log(JSON.stringify({status:'passed',desktop:'1440x1050',mobile:'390x844',...want,latestDate,checks:['search','empty search','industry detail links','print restore','unique IDs','mobile overflow','latest independent date','no JS errors']}));
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
