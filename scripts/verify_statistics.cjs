const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.SURVIVALFLOW_TEST_URL;
if (!base || !process.env.SURVIVALFLOW_TEST_DATA) throw Error('Use the isolated verify_statistics.py runner');
const out = path.resolve(__dirname, '../docs/validation/statistics');
fs.mkdirSync(out, {recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  const page = await browser.newPage({viewport:{width:1024,height:600}});
  const errors=[];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('response', r=>{if(r.status()>=400)errors.push(`${r.status()} ${r.url()}`);});
  const checks=[];
  try {
    await page.goto(base);
    const state=await (await page.request.get(base+'/api/state')).json();
    assert(state.jobs.length>=6);
    const job=state.jobs[0];
    const csv=`job_id,captured,views,likes,comments,shares\n${job.id},${new Date(Date.now()-60000).toISOString()},400,0,,\n${job.id},${new Date(Date.now()-120000).toISOString()},300,2,1,\n`;
    const imported=await page.request.post(base+'/api/metrics/import',{multipart:{file:{name:'statistics.csv',mimeType:'text/csv',buffer:Buffer.from(csv)}}});
    assert.equal(imported.status(),200);
    await page.getByRole('button',{name:'Статистика',exact:true}).click();
    await page.getByLabel('Вид статистики').selectOption('channels');
    await page.getByLabel('Канал статистики').selectOption(job.account.id);
    await page.getByRole('table',{name:'Сводка каналов'}).waitFor();
    await page.waitForFunction(()=>document.querySelector('table[aria-label="Сводка каналов"] tbody tr td:nth-child(2)')?.textContent?.startsWith('400'));
    const row=page.getByRole('table',{name:'Сводка каналов'}).locator('tbody tr');
    assert.equal(await row.count(),1);
    assert((await row.locator('td').nth(1).innerText()).startsWith('400'));
    assert((await row.locator('td').nth(2).innerText()).startsWith('0'));
    assert((await row.locator('td').nth(3).innerText()).startsWith('—'));
    await page.getByLabel('Канал статистики').selectOption('all');
    const downloadPromise=page.waitForEvent('download');
    await page.getByRole('button',{name:'Экспорт CSV',exact:true}).click();
    const download=await downloadPromise;
    await download.saveAs(path.join(out,'channels.csv'));
    const exported=fs.readFileSync(path.join(out,'channels.csv'),'utf8');
    assert(exported.includes('"views_coverage"'));
    assert(exported.includes('"400"'));
    assert.equal(exported.split('\r\n').filter(Boolean).length,state.accounts.length+1,'exports all filtered pages');
    for(const platform of ['youtube','tiktok','all']) {
      await page.getByLabel('Платформа статистики').selectOption(platform);
      for(const width of [1024,1366]) {
        await page.setViewportSize({width,height:600});
        await page.evaluate(()=>document.fonts.ready);
        const bounds=await page.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,overflow:[...document.querySelectorAll('.e-content')].some(e=>e.scrollHeight>e.clientHeight+2)}));
        assert.equal(bounds.scrollWidth,width);
        assert.equal(bounds.scrollHeight,600);
        assert.equal(bounds.overflow,false,`${platform} ${width}: inner overflow`);
        await page.screenshot({path:path.join(out,`channels-${platform}-${width}.png`)});
        checks.push({platform,...bounds});
      }
    }
    const next=page.getByRole('button',{name:'Следующая страница',exact:true});
    while(await next.isEnabled()) await next.click();
    assert((await page.getByRole('table',{name:'Сводка каналов'}).innerText()).includes('Нет снимков'));
    assert.equal(await page.locator('.e-content').evaluate(e=>e.scrollHeight>e.clientHeight+2),false);
    await page.screenshot({path:path.join(out,'channels-last-page.png')});
    await page.getByLabel('Вид статистики').selectOption('videos');
    await page.getByLabel('Канал статистики').selectOption(job.account.id);
    await page.getByRole('table').waitFor();
    assert((await page.getByRole('table').innerText()).includes('400'));
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({checks,errors,correctLatest:true,zeroAndMissing:true,isolated:true},null,2));
    console.log('Statistics UI: latest snapshot, null/zero, channel/platform filters, six viewport checks passed');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
