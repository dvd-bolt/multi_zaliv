// Local UI test. Requires a fresh, isolated API on port 8766; never publishes.
const { chromium } = require('../frontend/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.SURVIVALFLOW_TEST_URL || 'http://127.0.0.1:8766';
const fixture = process.env.SURVIVALFLOW_TEST_FIXTURES;
if (!fixture)
  throw new Error('Set SURVIVALFLOW_TEST_FIXTURES to the synthetic pytest artifact folder');
const output = path.resolve(__dirname, '../docs/validation/v0.1');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const checks = [];
  const snapshot = async (name) => {
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(200);
    const bounds = await page.evaluate(() => ({
      width: innerWidth,
      height: innerHeight,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      overflows: [
        ...document.querySelectorAll('.e-inspector,[data-inspector],.e-content,.e-assets'),
      ]
        .filter((e) => e.scrollHeight > e.clientHeight + 2)
        .map((e) => e.className || e.getAttribute('data-inspector')),
    }));
    assert.equal(bounds.scrollHeight, bounds.height, `${name}: page height`);
    assert.equal(bounds.scrollWidth, bounds.width, `${name}: page width`);
    assert.deepEqual(bounds.overflows, [], `${name}: inner overflow`);
    if (await page.locator('.e-inspector').isVisible()) {
      const tab = await page.locator('[data-inspector]').boundingBox(),
        bar = await page.locator('.e-savebar').boundingBox();
      assert(tab.y + tab.height <= bar.y + 1, `${name}: inspector overlaps action`);
    }
    await page.screenshot({ path: path.join(output, name + '.png') });
    checks.push({ name, ...bounds });
  };
  try {
    await page.goto(base);
    await page.getByText('Исходники', { exact: true }).waitFor();
    await snapshot('empty-editor');
    const initial = await (await page.request.get(base + '/api/state')).json();
    assert.equal(initial.assets.length, 0, 'UI test needs an empty isolated database');
    await page.getByRole('button', { name: 'Каналы', exact: true }).click();
    for (let i = 0; i < 6; i++) {
      await page.getByRole('button', { name: 'Добавить аккаунт', exact: true }).click();
      await page
        .getByLabel('Платформа', { exact: true })
        .selectOption(i < 3 ? 'youtube' : 'tiktok');
      await page.getByLabel('Название для вас', { exact: true }).fill('Локальный тест ' + (i + 1));
      await page.getByRole('button', { name: 'Добавить', exact: true }).click();
      await page.locator('dialog').waitFor({ state: 'hidden' });
    }
    await snapshot('channels');
    await page.getByRole('button', { name: 'Монтаж', exact: true }).click();
    for (let i = 0; i < 3; i++) {
      await page
        .locator('input[type=file][accept="video/*,.mkv"]')
        .setInputFiles(path.join(fixture, `sample-${i}.mp4`));
      await page.waitForFunction((n) => document.querySelectorAll('.e-source').length === n, i + 1);
      await page.waitForFunction(
        (name) => document.querySelector('.e-source.active .e-source-title')?.textContent === name,
        `sample-${i}.mp4`,
      );
    }
    await page
      .locator('input[type=file][accept^="image/png"]')
      .setInputFiles(path.join(fixture, 'banner.png'));
    await page.getByLabel('Файл баннера', { exact: true }).waitFor();
    await page.waitForFunction(
      () => document.querySelectorAll('.e-file-picker option').length === 2,
    );
    await page.getByRole('button', { name: 'Таймкод', exact: true }).click();
    await page.getByLabel('Начало, с', { exact: true }).fill('0.5');
    await page.getByLabel('Длительность, с', { exact: true }).fill('0.5');
    await page.getByRole('button', { name: 'К баннеру', exact: true }).click();
    for (const button of await page.locator('.e-target').all()) await button.click();
    await snapshot('editor-banner');
    await page
      .getByRole('button', { name: 'Текст и дополнительные параметры', exact: true })
      .click();
    await snapshot('banner-dialog');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog').count(), 0);
    await page.getByRole('tab', { name: 'Варианты', exact: true }).click();
    await page.getByRole('slider', { name: /Яркость/ }).press('ArrowRight');
    await snapshot('editor-filters');
    await page.getByRole('tab', { name: 'Публикация', exact: true }).click();
    await page.getByLabel('Название для YouTube', { exact: true }).fill('Только локальный тест');
    await snapshot('editor-publish');
    await page.getByRole('button', { name: 'В очередь · 6 вариантов', exact: true }).click();
    await page.getByRole('button', { name: 'Создать варианты', exact: true }).click();
    await page.getByRole('heading', { name: 'Очередь', exact: true }).waitFor();
    await snapshot('queue');
    await page.getByRole('button', { name: 'Следующая страница', exact: true }).click();
    await snapshot('queue-page-2');
    await page.getByRole('button', { name: 'Статистика', exact: true }).click();
    await snapshot('stats');
    await page.getByRole('button', { name: 'Импорт CSV', exact: true }).click();
    await snapshot('stats-import-dialog');
    const jobs = (await (await page.request.get(base + '/api/state')).json()).jobs;
    const csv =
      'job_id,captured,views,likes,comments,shares\n' +
      jobs.map((j, i) => `${j.id},2026-01-01T00:00:00Z,${10 + i},2,,\n`).join('') +
      `${jobs[0].id},2026-01-02T00:00:00Z,20,3,,\n`;
    await page
      .locator('input[type=file][accept=".csv"]')
      .setInputFiles({ name: 'metrics.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
    await page.locator('dialog').waitFor({ state: 'hidden' });
    await page.getByText('youtube · Импорт CSV пользователя', { exact: true }).first().waitFor();
    await snapshot('stats-imported');
    await page.getByRole('button', { name: 'Динамика', exact: true }).click();
    await snapshot('stats-history');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Настройки', exact: true }).click();
    await snapshot('settings');
    await page.getByRole('button', { name: 'Копия базы', exact: true }).click();
    await page.getByText('Резервная копия:', { exact: false }).waitFor();
    await page.locator('.toast button').click();
    for (const width of [1366, 1280, 1024]) {
      await page.setViewportSize({ width, height: 600 });
      await page.getByRole('button', { name: 'Монтаж', exact: true }).click();
      for (const tab of ['Баннер', 'Варианты', 'Публикация']) {
        await page.getByRole('tab', { name: tab, exact: true }).click();
        await snapshot(
          `layout-${width}-${tab === 'Баннер' ? 'banner' : tab === 'Варианты' ? 'filters' : 'publish'}`,
        );
      }
    }
    await page.setViewportSize({ width: 1024, height: 600 });
    await page.getByRole('button', { name: 'Запустить', exact: true }).click();
    await page.waitForFunction(() => document.body.innerText.includes('Очередь запущена'));
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(output, 'ui-results.json'),
      JSON.stringify(
        { base, checks, errors, queueStarted: true, closedBeforeRendering: true },
        null,
        2,
      ),
    );
    console.log(JSON.stringify({ checks: checks.length, errors, queuedVariants: 6 }));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
