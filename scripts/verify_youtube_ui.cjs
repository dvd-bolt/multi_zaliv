const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.SURVIVALFLOW_TEST_URL;
if (base !== 'http://127.0.0.1:8773') throw new Error('Use the isolated runner');
const output = path.resolve(__dirname, '../docs/validation/youtube');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
  const errors = [], checks = [];
  page.on('pageerror', e => errors.push(e.message));
  async function snapshot(name) {
    await page.evaluate(() => document.fonts.ready);
    const bounds = await page.evaluate(() => {
      const dialog = document.querySelector('dialog[open]');
      const r = dialog?.getBoundingClientRect();
      return { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight,
        dialog: r ? { top: r.top, bottom: r.bottom, left: r.left, right: r.right,
          scroll: dialog.scrollHeight > dialog.clientHeight + 2 } : null };
    });
    assert.equal(bounds.width, 1024, name); assert.equal(bounds.height, 600, name);
    if (bounds.dialog) {
      assert(bounds.dialog.top >= 0 && bounds.dialog.bottom <= 600, name);
      assert(bounds.dialog.left >= 0 && bounds.dialog.right <= 1024, name);
      assert.equal(bounds.dialog.scroll, false, name);
    }
    checks.push({ name, ...bounds });
    await page.screenshot({ path: path.join(output, name + '.png') });
  }
  try {
    await page.goto(base);
    await page.getByRole('button', { name: 'Каналы', exact: true }).first().click();
    await snapshot('channels');
    await page.getByRole('button', { name: 'Все видео', exact: true }).click();
    await page.getByText('Видео: 57', { exact: false }).waitFor();
    await snapshot('all-videos-first');
    await page.getByRole('button', { name: 'Далее', exact: true }).click();
    await page.getByText('2 / 19', { exact: false }).waitFor();
    await snapshot('all-videos-page2');
    await page.getByRole('button', { name: 'Обновить с YouTube', exact: true }).click();
    await page.getByRole('button', { name: 'Обновить с YouTube', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Закрыть диалог' }).click();
    await page.getByRole('button', { name: 'Статистика', exact: true }).first().click();
    await page.getByRole('tab', { name: 'Ролики', exact: true }).click();
    await page.getByText('Ручной ролик 56', { exact: false }).first().waitFor();
    await snapshot('statistics-manual-videos');
    await page.getByRole('button', { name: 'Настройки', exact: true }).first().click();
    await page.getByRole('button', { name: 'Подключения', exact: true }).click();
    await snapshot('connections');
    await page.getByRole('button', { name: 'Обложки YouTube', exact: true }).click();
    const toggle = page.getByRole('checkbox', { name: 'Генерировать и добавлять обложку при загрузке на YouTube' });
    assert(await toggle.isChecked());
    await snapshot('cover-settings');
    const [saved] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/settings') && r.request().method() === 'PATCH'),
      toggle.click(),
    ]);
    assert.equal(saved.status(), 200);
    await page.getByRole('button', { name: 'Закрыть диалог' }).click();
    await page.getByRole('button', { name: 'Очередь', exact: true }).first().click();
    await page.getByRole('button', { name: 'Подробнее', exact: true }).click();
    await snapshot('job-cover-error');
    await page.getByRole('button', { name: 'Ролик удалён — загрузить заново', exact: true }).click();
    await snapshot('reupload-confirmation');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'ui-results.json'), JSON.stringify({ fixture: 'synthetic, no remote writes', checks, errors }, null, 2));
    console.log(JSON.stringify({ passed: checks.length, errors }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
