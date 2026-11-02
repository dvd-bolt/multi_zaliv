const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.SURVIVALFLOW_TEST_URL;
if (!base || base.endsWith(':8765')) throw new Error('Use the isolated Python runner');
const output = path.resolve(__dirname, '../docs/validation/stats-push');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
  const errors = [];
  const checks = [];
  page.on('pageerror', (e) => errors.push(e.message));
  let statusOverride;
  await page.route('**/api/state', async (route) => {
    const response = await route.fetch();
    const state = await response.json();
    if (statusOverride) state.stats_push = { ...state.stats_push, ...statusOverride };
    await route.fulfill({ response, json: state });
  });
  async function snapshot(name) {
    await page.evaluate(() => document.fonts.ready);
    const bounds = await page.evaluate(() => ({
      width: innerWidth, height: innerHeight,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      overflow: [...document.querySelectorAll('.e-content,.e-settings,.e-inspector,[data-inspector],.e-assets')]
        .filter((e) => e.scrollHeight > e.clientHeight + 2 || e.scrollWidth > e.clientWidth + 2)
        .map((e) => e.className || e.getAttribute('data-inspector')),
      outside: [...document.querySelectorAll('.e-settings button,.e-settings input,.e-settings p,.e-settings label')]
        .filter((e) => { const r = e.getBoundingClientRect(); return r.bottom > innerHeight - 26 || r.right > innerWidth; })
        .map((e) => e.textContent),
    }));
    assert.equal(bounds.scrollHeight, 600, name);
    assert.equal(bounds.scrollWidth, 1024, name);
    assert.deepEqual(bounds.overflow, [], name);
    assert.deepEqual(bounds.outside, [], name);
    await page.screenshot({ path: path.join(output, name + '.png') });
    checks.push({ name, ...bounds });
  }
  try {
    await page.goto(base);
    await page.getByRole('button', { name: 'Настройки', exact: true }).first().click();
    await snapshot('general');
    await page.getByRole('button', { name: 'Подключения', exact: true }).click();
    const toggle = page.getByRole('checkbox', { name: 'Отправлять статистику на сервер' });
    await toggle.waitFor();
    assert(await toggle.isChecked(), 'enabled by default with connection');
    await snapshot('connections-first-send');
    const [off] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/api/stats-push') && r.request().method() === 'PATCH'),
      toggle.click(),
    ]);
    assert.equal(off.status(), 200);
    await page.getByRole('status').filter({ hasText: 'Статистика отправляется на сервер:' }).filter({ hasText: 'выключено' }).waitFor();
    await page.reload();
    await page.getByRole('button', { name: 'Настройки', exact: true }).first().click();
    await page.getByRole('button', { name: 'Подключения', exact: true }).click();
    assert(!(await toggle.isChecked()), 'opt-out survives reload');
    await toggle.click();
    await page.getByRole('status').filter({ hasText: 'ещё не отправлялась' }).waitFor();
    const row = page.getByRole('status').filter({ hasText: 'Статистика отправляется на сервер:' });
    for (const [result, expected] of [['ok', 'последний раз 01.11, 13:05 МСК'], ['not_ready', 'сервер ещё не обновлён (404)'], ['network_error', 'ошибка сети'], ['auth_error', 'сервер не принял код']]) {
      statusOverride = { enabled: true, result, http_status: result === 'not_ready' ? 404 : result === 'auth_error' ? 401 : 200,
        last_success: '2026-11-01T10:05:00+00:00', attempted_at: '2026-11-01T10:10:00+00:00' };
      // Reload to apply the controlled display fixture; no external requests are made.
      await page.reload();
      await page.getByRole('button', { name: 'Настройки', exact: true }).first().click();
      await page.getByRole('button', { name: 'Подключения', exact: true }).click();
      await row.filter({ hasText: expected }).waitFor();
      await snapshot('connections-' + result);
    }
    await page.getByRole('button', { name: 'Данные', exact: true }).click();
    await snapshot('storage');
    await page.getByRole('button', { name: 'Монтаж', exact: true }).click();
    await snapshot('editor');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'ui-results.json'), JSON.stringify({ checks, errors, isolated: true, externalRequests: false }, null, 2));
    console.log('Statistics push UI: defaults, persistence, MSK time, result states and 1024x600 bounds passed.');
  } finally { await browser.close(); }
})().catch((e) => { console.error(e); process.exit(1); });
