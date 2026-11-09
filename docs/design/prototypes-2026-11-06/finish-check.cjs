const { chromium } = require('../../../frontend/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  const checks = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
    await page.goto('http://127.0.0.1:8795/');
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(__dirname, 'shots', 'comparison.png'), fullPage: true });
    for (const width of [1440, 1024, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      checks.push({ name: `gallery-${width}`, ok: await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1 && [...document.images].every(n => n.complete && n.naturalWidth > 0)) });
    }
    for (const theme of ['studio', 'precision', 'focus']) {
      await page.setViewportSize({ width: 1024, height: 600 });
      await page.goto(`http://127.0.0.1:8795/${theme}.html`);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      checks.push({ name: `${theme}: reduced motion`, ok: await page.locator('.btn').first().evaluate(n => getComputedStyle(n).transitionDuration === '0s') });
      for (const box of await page.locator('[data-channel]').all()) await box.uncheck();
      await page.locator('[data-action="launch"]').click();
      await page.locator('[data-action="demo-launch"]').click();
      checks.push({ name: `${theme}: zero channels blocked`, ok: await page.locator('#dialog-error').innerText().then(s => s.includes('один канал')) });
      await page.keyboard.press('Escape');
      await page.locator('[data-channel="0"]').check();
      await page.locator('[data-action="save"]').click();
      await page.reload();
      checks.push({ name: `${theme}: settings survive reload`, ok: (await page.locator('[data-channel]:checked').count()) === 1 });
      await page.evaluate(() => localStorage.clear());
    }
  } finally {
    await browser.close();
    const result = { checks, errors };
    fs.writeFileSync(path.join(__dirname, 'finish-check.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    if (errors.length || checks.some(c => !c.ok)) process.exitCode = 1;
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
