// UI regressions in the isolated database prepared by verify_ui.cjs. No uploads to platforms.
const { chromium } = require('../frontend/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.SURVIVALFLOW_TEST_URL;
const fixture = process.env.SURVIVALFLOW_TEST_FIXTURES;
const out = path.resolve(__dirname, '../docs/validation/overnight');
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  let page;
  try {
    page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
    const exceptions = [],
      badResponses = [],
      badRequests = [],
      consoleErrors = [];
    page.on('pageerror', (e) => exceptions.push(e.message));
    page.on('response', (r) => {
      if (r.status() >= 400) badResponses.push({ url: r.url(), status: r.status() });
    });
    page.on('requestfailed', (r) => {
      if (!r.failure()?.errorText.includes('ERR_ABORTED')) badRequests.push(r.failure()?.errorText);
    });
    page.on('console', (m) => {
      if (m.type() === 'error') consoleErrors.push(m.text());
    });
    const snap = async (name) => {
      await page.waitForTimeout(150);
      const box = await page.evaluate(() => ({
        w: innerWidth,
        h: innerHeight,
        sw: document.documentElement.scrollWidth,
        sh: document.documentElement.scrollHeight,
        inner: [...document.querySelectorAll('.e-content,.e-inspector,[data-inspector],.e-assets')]
          .filter((e) => e.scrollHeight > e.clientHeight + 2)
          .map((e) => e.className),
        dialog: [...document.querySelectorAll('dialog[open]')].map((e) => ({
          h: e.clientHeight,
          sh: e.scrollHeight,
          w: e.clientWidth,
          sw: e.scrollWidth,
        })),
      }));
      assert.equal(box.sw, box.w, name + ' horizontal overflow');
      assert.equal(box.sh, box.h, name + ' vertical overflow');
      assert.deepEqual(box.inner, [], name + ' inner overflow');
      for (const d of box.dialog) {
        assert(d.sh <= d.h + 2, name + ' dialog height');
        assert(d.sw <= d.w + 2, name + ' dialog width');
      }
      await page.screenshot({ path: path.join(out, name + '.png') });
    };
    const initial = await (await page.request.get(base + '/api/state')).json();
    assert.equal(initial.assets.filter((a) => a.kind === 'video').length, 3);
    for (let i = 6; i < 30; i++) {
      const response = await page.request.post(base + '/api/accounts', {
        data: { platform: i % 2 ? 'tiktok' : 'youtube', name: 'Длинное имя ' + 'Ж'.repeat(48) + i },
      });
      assert.equal(response.status(), 200);
    }
    const bytes = fs.readFileSync(path.join(fixture, 'sample-0.mp4'));
    for (let i = 0; i < 4; i++) {
      const response = await page.request.post(base + '/api/assets?kind=video', {
        multipart: {
          file: {
            name: 'Очень длинное имя ' + 'W'.repeat(160) + i + '.mp4',
            mimeType: 'video/mp4',
            buffer: bytes,
          },
        },
      });
      assert.equal(response.status(), 200);
    }
    await page.request.patch(base + '/api/settings', { data: { project_name: 'W'.repeat(80) } });
    await page.goto(base);
    await page.locator('.e-source-select').first().waitFor();
    await page.getByRole('tab', { name: 'Варианты', exact: true }).click();
    const brightness = page.getByRole('slider', { name: 'Яркость', exact: true });
    await brightness.press('ArrowRight');
    const changed = await brightness.inputValue();
    await page.locator('.e-source.active .e-source-select').click();
    assert.equal(
      await brightness.inputValue(),
      changed,
      'Clicking the active source must preserve the draft',
    );
    let saves = 0;
    page.on('request', (r) => {
      if (r.method() === 'PUT' && r.url().endsWith('/edit')) saves++;
    });
    await page.getByRole('button', { name: 'Сохранить', exact: true }).dblclick();
    await page.getByRole('button', { name: 'Сохранено', exact: true }).waitFor();
    assert.equal(saves, 1, 'Double click must save once');
    await page.reload();
    await page.getByRole('tab', { name: 'Варианты', exact: true }).click();
    assert.equal(await brightness.inputValue(), changed);
    await page.getByRole('tab', { name: 'Баннер', exact: true }).click();
    await page.getByRole('button', { name: 'К баннеру', exact: true }).click();
    await page.locator('.e-overlay img').waitFor();
    await page
      .getByRole('slider', { name: 'Позиция по горизонтали, %', exact: true })
      .press('Home');
    await page.getByRole('slider', { name: 'Позиция по вертикали, %', exact: true }).press('Home');
    const frame = await page.locator('.e-preview').boundingBox(),
      overlay = await page.locator('.e-overlay').boundingBox();
    assert(
      overlay.x >= frame.x - 1 && overlay.y >= frame.y - 1,
      'Banner must stay inside preview at an edge',
    );
    await page.getByRole('button', { name: 'До / после', exact: true }).click();
    assert.equal(await page.locator('.e-overlay').count(), 0);
    await page
      .locator('.e-viewer-tools')
      .getByRole('button', { name: 'Исходник', exact: true })
      .click();
    await page.getByRole('button', { name: 'Скрыть баннер', exact: true }).click();
    assert.equal(await page.locator('.e-overlay').count(), 0);
    await page.getByRole('button', { name: 'Показать баннер', exact: true }).click();
    await page.locator('.e-target').first().click();
    await page.getByRole('button', { name: 'Таймкод', exact: true }).click();
    await page.getByLabel('Начало, с', { exact: true }).fill('100');
    await page.getByRole('button', { name: 'В очередь · 1 вариантов', exact: true }).click();
    await page.getByRole('button', { name: 'Создать варианты', exact: true }).click();
    await page.locator('dialog').getByRole('alert').waitFor();
    assert.match(await page.locator('dialog').getByRole('alert').innerText(), /конец ролика/);
    await snap('visible-validation-error');
    await page.keyboard.press('Escape');
    await page.getByLabel('Начало, с', { exact: true }).fill('0.5');
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await page.getByRole('button', { name: 'Сохранено', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Все аккаунты (30)', exact: true }).click();
    await snap('target-pagination');
    for (let i = 0; i < 4; i++)
      await page
        .locator('dialog')
        .getByRole('button', { name: 'Следующая страница', exact: true })
        .click();
    assert.equal(await page.locator('.target-dialog input').count(), 6);
    await snap('target-last-page');
    await page.keyboard.press('Escape');
    await page.locator('input[type=file][accept^="image/png"]').setInputFiles({
      name: 'broken.png',
      mimeType: 'image/png',
      buffer: Buffer.from('not-an-image'),
    });
    await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').innerText(), /прочитать файл/);
    await page.getByRole('button', { name: 'Скрыть сообщение', exact: true }).click();
    await page
      .locator('.e-assets')
      .getByRole('button', { name: 'Следующая страница', exact: true })
      .click();
    await page
      .locator('.e-assets')
      .getByRole('button', { name: 'Следующая страница', exact: true })
      .click();
    await page.locator('.e-source-select').click();
    await snap('long-source-name');
    const before = (await (await page.request.get(base + '/api/state')).json()).assets.filter(
      (a) => a.kind === 'video',
    );
    const archived = before[before.length - 1];
    await page
      .getByRole('button', { name: 'Убрать ' + archived.name + ' из списка', exact: true })
      .click();
    await page.getByRole('button', { name: 'Убрать из списка', exact: true }).click();
    await page.locator('dialog').waitFor({ state: 'hidden' });
    await page.waitForFunction(() =>
      document.querySelector('.e-assets-head')?.textContent.includes('6'),
    );
    assert.equal((await page.request.get(base + `/api/assets/${archived.id}/file`)).status(), 200);
    assert.equal(await page.locator('.e-source').count(), 3);
    await snap('after-archive');
    for (const name of ['Баннер', 'Варианты', 'Публикация']) {
      await page.getByRole('tab', { name, exact: true }).click();
      await snap('editor-' + name);
    }
    for (const name of ['Очередь', 'Каналы', 'Статистика', 'Настройки']) {
      await page.getByRole('button', { name, exact: true }).click();
      await snap('page-' + name);
    }
    await page.getByRole('button', { name: 'Каналы', exact: true }).click();
    await page.getByRole('button', { name: 'Добавить аккаунт', exact: true }).click();
    await page.getByLabel('Название для вас', { exact: true }).fill('   ');
    assert(await page.getByRole('button', { name: 'Добавить', exact: true }).isDisabled());
    await page.keyboard.press('Escape');
    let offline = true;
    await page.route('**/api/state', (route) =>
      offline
        ? route.fulfill({
            status: 503,
            contentType: 'application/json',
            body: '{"detail":"QA offline"}',
          })
        : route.continue(),
    );
    await page
      .getByText('Нет связи с локальным сервером. Ждём восстановления соединения…', { exact: true })
      .waitFor();
    await snap('connection-lost');
    offline = false;
    await page
      .getByText('Нет связи с локальным сервером. Ждём восстановления соединения…', { exact: true })
      .waitFor({ state: 'hidden' });
    await page.unroute('**/api/state');
    assert.deepEqual(exceptions, []);
    assert.deepEqual(badRequests, []);
    assert(
      badResponses.every(
        (r) =>
          (r.status === 400 && r.url.includes('/api/assets')) ||
          (r.status === 503 && r.url.endsWith('/api/state')),
      ),
      JSON.stringify(badResponses),
    );
    assert(
      consoleErrors.every((s) => s.includes('400') || s.includes('503')),
      JSON.stringify(consoleErrors),
    );
    const state = await (await page.request.get(base + '/api/state')).json();
    assert.equal(state.jobs.length, 6);
    assert(state.jobs.every((j) => !j.approved && !j.external_id));
    const report = {
      activeDraft: 'passed',
      modalErrors: 'passed',
      singleSave: 'passed',
      archivePreservesFiles: 'passed',
      longNames: 'passed',
      thirtyAccounts: 'passed',
      previewControls: 'passed',
      connectionRecovery: 'passed',
      consoleExceptions: exceptions,
      failedRequests: badRequests,
      expectedResponses: badResponses,
    };
    fs.writeFileSync(path.join(out, 'edge-results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    if (page) {
      await page.screenshot({ path: path.join(out, 'failed-edge.png') });
      console.log(await page.locator('body').innerText());
    }
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
