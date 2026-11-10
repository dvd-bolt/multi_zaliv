// Real local API and disposable database; no credentials, worker, or platform requests.
const { chromium } = require('../frontend/node_modules/playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs'),
  os = require('node:os'),
  path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'frameflow-precision-'));
const out = path.resolve(
  process.env.FRAMEFLOW_VALIDATION_OUT || path.join(root, 'docs/validation/precision-mono'),
);
fs.mkdirSync(out, { recursive: true });
const base = 'http://127.0.0.1:8793';
const fixture = `
import os
from pathlib import Path
import uvicorn
from PIL import Image
import backend.app as module
from backend.config import DATA, binary
from backend.db import Session, Setting, settings
from backend.media import command
assert DATA != Path.cwd() / 'data'
module.ensure_queue_worker = lambda: None
with Session.begin() as db:
 db.get(Setting, 'application').value = {**settings(db), 'paused': True, 'buba_required': False, 'one_per_platform': False, 'auto_publish': False}
for i in range(3):
 command([binary('ffmpeg'), '-v','error','-y','-f','lavfi','-i','testsrc2=size=90x160:rate=6:duration=5','-vf',f'hue=h={i*60}','-c:v','libx264','-pix_fmt','yuv420p',str(DATA/f'source-{i}.mp4')])
Image.new('RGBA',(200,60),(240,240,240,220)).save(DATA/'banner.png')
uvicorn.run(module.app, host='127.0.0.1', port=8793, log_level='error')
`;
const log = fs.openSync(path.join(directory, 'server.log'), 'a');
const server = spawn(path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'), ['-c', fixture], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', log, log],
  env: { ...process.env, SURVIVALFLOW_DATA: directory, SURVIVALFLOW_PORT: '8793' },
});
const report = {
  isolatedData: directory,
  noPlatformRequests: true,
  checks: [],
  layouts: [],
  errors: [],
};
let browser;
function check(name, condition, details) {
  report.checks.push({ name, ok: !!condition, details });
  assert(condition, name);
}
(async () => {
  try {
    for (let i = 0; i < 150; i++) {
      try {
        if ((await fetch(base + '/api/health')).ok) break;
      } catch {}
      await new Promise((r) => setTimeout(r, 100));
    }
    const health = await (await fetch(base + '/api/health')).json();
    check(
      'API uses isolated data',
      path.resolve(health.data_directory) === path.resolve(directory),
    );
    browser = await chromium.launch({ channel: process.platform === 'win32' ? 'chrome' : undefined, headless: true });
    const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
    page.on('pageerror', (e) => report.errors.push(e.message));
    page.on('response', (r) => {
      if (r.status() >= 400) report.errors.push(`${r.status()} ${r.url()}`);
    });
    page.on('request', (r) => {
      if (!r.url().startsWith(base) && !r.url().startsWith('data:') && !r.url().startsWith('blob:'))
        report.errors.push('Unexpected external request');
    });
    const snapshot = async (name, desktop = true) => {
      await page.evaluate(() => document.fonts.ready);
      const bounds = await page.evaluate(() => ({
        width: innerWidth,
        height: innerHeight,
        sw: document.documentElement.scrollWidth,
        sh: document.documentElement.scrollHeight,
        overflow: [...document.querySelectorAll('.e-inspector,[data-inspector],.e-assets,.release-main,.release-side,.e-nav')]
          .filter((n) => n.scrollHeight > n.clientHeight + 2)
          .map((n) => n.className),
        contentBottom: Math.max(
          0,
          ...[...document.querySelectorAll('[data-inspector] > *')].map(
            (n) => n.getBoundingClientRect().bottom,
          ),
        ),
        saveTop: document.querySelector('.e-savebar')?.getBoundingClientRect().top,
      }));
      report.layouts.push({ name, ...bounds });
      await page.screenshot({ path: path.join(out, name + '.png'), fullPage: !desktop });
      check(`${name}: horizontal bounds`, bounds.sw <= bounds.width + 1);
      if (desktop) {
        check(`${name}: vertical bounds`, bounds.sh <= bounds.height + 1);
        check(`${name}: no clipped panels`, !bounds.overflow.length, bounds.overflow);
        if (bounds.saveTop)
          check(
            `${name}: content above launch`,
            bounds.contentBottom <= bounds.saveTop + 1,
            bounds,
          );
      }
    };
    const dialogShot = async (name) => {
      const bounds = await page
        .getByRole('dialog')
        .evaluate((n) => ({
          top: n.getBoundingClientRect().top,
          bottom: n.getBoundingClientRect().bottom,
          scroll: n.scrollHeight > n.clientHeight + 1,
        }));
      await page.screenshot({ path: path.join(out, name + '.png') });
      check(
        `${name}: dialog fits`,
        bounds.top >= 0 && bounds.bottom <= 600 && !bounds.scroll,
        bounds,
      );
    };
    await page.goto(base);
    await page.getByText('Исходники', { exact: true }).waitFor();
    await snapshot('empty');
    check('Precision is production theme', (await page.locator('.ed.precision').count()) === 1);
    const palette = await page
      .locator('.ed')
      .evaluate((n) =>
        ['--e-bg', '--e-surface', '--e-text', '--e-accent'].map((k) =>
          getComputedStyle(n).getPropertyValue(k).trim(),
        ),
      );
    check(
      'neutral monochrome tokens',
      palette.every((c) => /^#([0-9a-f]{2})\1\1$/i.test(c)),
      palette,
    );
    for (let i = 0; i < 6; i++) {
      const response = await page.request.post(base + '/api/accounts', {
        data: {
          platform: i < 3 ? 'youtube' : 'tiktok',
          name: `Локальный канал ${i + 1} с длинным названием для проверки`,
        },
      });
      check(`create test channel ${i + 1}`, response.ok());
    }
    await page.reload();
    for (let i = 0; i < 3; i++) {
      await page
        .locator('input[accept="video/*,.mkv"]')
        .setInputFiles({
          name: `Эпизод ${i + 1} — очень длинное название исходного видео.mp4`,
          mimeType: 'video/mp4',
          buffer: fs.readFileSync(path.join(directory, `source-${i}.mp4`)),
        });
      await page.waitForFunction((n) => document.querySelectorAll('.e-source').length === n, i + 1);
      await page.locator('.ed[aria-busy="false"]').waitFor();
    }
    await page
      .locator('input[accept^="image/png"]')
      .setInputFiles(path.join(directory, 'banner.png'));
    await page.waitForFunction(
      () => document.querySelectorAll('.e-file-picker option').length === 2,
    );
    await page.locator('.ed[aria-busy="false"]').waitFor();
    await page.getByRole('button', { name: 'Таймкод', exact: true }).click();
    await page.getByLabel('Начало, с', { exact: true }).fill('1');
    await page.getByLabel('Длительность, с', { exact: true }).fill('1');
    await page.getByRole('button', { name: 'К баннеру', exact: true }).click();
    for (const button of await page.locator('.e-target').all())
      if ((await button.getAttribute('aria-pressed')) !== 'true') await button.click();
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await page.getByRole('button', { name: 'Сохранено', exact: true }).waitFor();
    await page.locator('.ed[aria-busy="false"]').waitFor();
    await page.reload();
    await page.locator('.e-source.active').waitFor();
    await page.locator('.e-source-select').filter({ hasText: 'Эпизод 3' }).click();
    await page.locator('.ed[aria-busy="false"]').waitFor();
    check(
      'six channels persist',
      (await page.locator('.e-target[aria-pressed="true"]').count()) === 6,
    );
    check(
      'banner timing persists',
      (await page.getByLabel('Начало, с', { exact: true }).inputValue()) === '1',
    );
    for (const size of [
      { width: 1440, height: 900 },
      { width: 1280, height: 800 },
      { width: 1024, height: 600 },
    ]) {
      await page.setViewportSize(size);
      for (const tab of ['Баннер', 'Варианты', 'Публикация']) {
        await page.getByRole('tab', { name: tab, exact: true }).click();
        await snapshot(
          `${size.width}-${size.height}-${tab === 'Баннер' ? 'banner' : tab === 'Варианты' ? 'filters' : 'publish'}`,
        );
      }
    }
    await page.getByRole('tab', { name: 'Баннер', exact: true }).click();
    await page.keyboard.press('ArrowRight');
    check(
      'keyboard switches inspector tab',
      (await page
        .getByRole('tab', { name: 'Варианты', exact: true })
        .getAttribute('aria-selected')) === 'true',
    );
    await page.getByRole('slider', { name: /Яркость/ }).press('ArrowRight');
    check(
      'filter preview responds',
      await page
        .locator('.e-video')
        .evaluate((n) => n.style.filter !== 'brightness(1) contrast(1) saturate(1)'),
    );
    await page.getByRole('tab', { name: 'Баннер', exact: true }).click();
    await page
      .getByRole('button', { name: 'Текст и дополнительные параметры', exact: true })
      .click();
    await dialogShot('banner-dialog');
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    check(
      'dialog restores focus',
      (await page.evaluate(() => document.activeElement.textContent.trim())) ===
        'Текст и дополнительные параметры',
    );
    await page.getByRole('button', { name: 'Воспроизвести', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.e-video').paused);
    check('real preview plays', true);
    await page.getByRole('button', { name: 'Остановить воспроизведение', exact: true }).click();
    // «К публикации» opens the saved release screen (replaces the former autopublish dialog).
    await page.getByRole('button', { name: /^К публикации/ }).click();
    await page.locator('.release-view').waitFor();
    await snapshot('release-step-1');
    await page.getByRole('tab', { name: /Каналы и время/ }).click();
    await snapshot('release-step-2');
    await page.getByRole('tab', { name: /Перед отправкой/ }).click();
    await snapshot('release-step-3');
    check(
      'release draft starts nothing',
      (await (await fetch(base + '/api/state')).json()).jobs.length === 0,
    );
    await page.getByRole('button', { name: 'Каналы', exact: true }).click();
    await snapshot('channels');
    await page.getByRole('button', { name: 'Добавить аккаунт', exact: true }).click();
    await dialogShot('new-account-dialog');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Очередь', exact: true }).click();
    await snapshot('queue-empty');
    await page.getByRole('button', { name: 'Статистика', exact: true }).click();
    for (const name of ['Обзор', 'Ролики', 'Каналы', 'Выплаты БУБА']) {
      const button = page.locator('.st-tabs').first().getByRole('tab', { name, exact: true });
      await button.click();
      await snapshot('statistics-' + name);
    }
    await page.getByRole('button', { name: 'Настройки', exact: true }).click();
    for (const button of await page.locator('.e-settings-tabs button').all()) {
      await button.click();
      await snapshot('settings-' + (await button.innerText()));
    }
    await page.getByRole('button', { name: 'Монтаж', exact: true }).click();
    for (const size of [
      { width: 768, height: 800 },
      { width: 390, height: 844 },
      { width: 320, height: 740 },
    ]) {
      await page.setViewportSize(size);
      await snapshot('compact-' + size.width, false);
    }
    await page.emulateMedia({ reducedMotion: 'reduce' });
    check(
      'reduced motion',
      await page
        .locator('.e-button')
        .first()
        .evaluate((n) => getComputedStyle(n).transitionDuration === '0s'),
    );
    const state = await (await page.request.get(base + '/api/state')).json();
    check('no publications or queue writes', state.jobs.length === 0);
    assert.deepEqual(report.errors, []);
  } catch (error) {
    report.errors.push(String(error));
    throw error;
  } finally {
    await browser?.close();
    server.kill();
    fs.closeSync(log);
    fs.writeFileSync(path.join(out, 'ui-results.json'), JSON.stringify(report, null, 2));
    console.log(
      JSON.stringify({
        checks: report.checks.length,
        failed: report.checks.filter((c) => !c.ok),
        errors: report.errors,
        layouts: report.layouts.length,
      }),
    );
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
