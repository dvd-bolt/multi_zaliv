const { chromium } = require('../frontend/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.SURVIVALFLOW_TEST_URL;
if (!base) throw new Error('Use an isolated API with already rendered synthetic jobs');
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(base);
    await page.getByRole('button', { name: 'Очередь', exact: true }).click();
    const output = path.resolve(__dirname, '../docs/validation/v0.1');
    await page.screenshot({ path: path.join(output, 'queue-ready.png') });
    await page.getByRole('button', { name: 'Разрешить отправку пакета', exact: true }).click();
    await page.getByRole('heading', { name: 'Подтвердить отправку пакета', exact: true }).waitFor();
    const dialog = await page
      .locator('dialog')
      .evaluate((e) => ({
        h: e.clientHeight,
        s: e.scrollHeight,
        bottom: e.getBoundingClientRect().bottom,
      }));
    assert(dialog.s <= dialog.h + 2 && dialog.bottom <= 600, 'Batch dialog must fit');
    assert.equal(await page.locator('.batch-item').count(), 4);
    await page.screenshot({ path: path.join(output, 'batch-approval.png') });
    await page
      .locator('dialog')
      .getByRole('button', { name: 'Следующая страница', exact: true })
      .click();
    assert.equal(await page.locator('.batch-item').count(), 2);
    await page.keyboard.press('Escape');
    const state = await (await page.request.get(base + '/api/state')).json();
    assert(
      state.jobs.every((j) => !j.approved && !j.external_id),
      'Inspecting a dialog must not approve a publication',
    );
    const response = await page.request.get(
      base + `/api/jobs/${state.jobs[0].id}/file?download=false`,
      { headers: { Range: 'bytes=0-31' } },
    );
    assert.equal(response.status(), 206);
    assert.equal((await response.body()).length, 32);
    await page.getByRole('button', { name: 'Подробнее', exact: true }).first().click();
    await page.getByRole('button', { name: 'Посмотреть MP4', exact: true }).click();
    await page.locator('.export-player').waitFor();
    await page.waitForFunction(() => document.querySelector('.export-player')?.readyState >= 2);
    await page.locator('.export-player').evaluate((video) => video.play());
    await page.waitForFunction(() => document.querySelector('.export-player')?.currentTime > 0);
    await page.screenshot({ path: path.join(output, 'export-preview.png') });
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    const report = {
      batchDialog: 'passed',
      pagination: 'passed',
      explicitApprovalUnchanged: 'passed',
      mp4RangeResponse: 'passed',
      mp4Playback: 'passed',
      errors,
    };
    fs.writeFileSync(path.join(output, 'ready-ui-results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
