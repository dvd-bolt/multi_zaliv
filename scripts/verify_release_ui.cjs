// «Выпуск» end to end on a disposable database: real API and UI, synthetic render, no platform requests.
const { chromium } = require('../frontend/node_modules/playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs'),
  os = require('node:os'),
  path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const data = fs.mkdtempSync(path.join(os.tmpdir(), 'frameflow-release-ui-'));
const out = path.resolve(
  process.env.FRAMEFLOW_VALIDATION_OUT || path.join(root, 'docs/validation/publication-flow'),
);
fs.mkdirSync(out, { recursive: true });
const base = 'http://127.0.0.1:8796';
const fixture = `
import hashlib, shutil
from pathlib import Path
import uvicorn
from PIL import Image
import backend.app as module
from backend import preflight, releases
from backend.config import DATA, ROOT, binary
from backend.db import Account, Asset, Job, Session, Setting, Variant, now, settings, uid
from backend.media import command, inspect
from backend.schemas import Edit
assert DATA != ROOT / 'data'
module.ensure_queue_worker = lambda: None
# Sign-in checks would open TikTok/Google: answer locally.
preflight.check_accounts = lambda db, ids, checks=None: {i: {'status': 'ok', 'detail': 'ok'} for i in ids}
with Session.begin() as db:
 db.get(Setting, 'application').value = {**settings(db), 'paused': True, 'buba_required': False, 'one_per_platform': True, 'interval': 30}
 for i, name in enumerate(['Клип 1 — сильное начало', 'Клип 2 — поворот', 'Клип 3 — развязка']):
  path = DATA / f'clip-{i}.mp4'
  command([binary('ffmpeg'), '-v','error','-y','-f','lavfi','-i','testsrc2=size=90x160:rate=6:duration=4','-vf',f'hue=h={i*80}','-c:v','libx264','-pix_fmt','yuv420p',str(path)])
  key = uid()
  db.add(Asset(id=key, kind='video', name=name + '.mp4', path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
   media=inspect(path, 'video', key), edit=Edit(title=name, caption=name, visibility='private').model_dump()))
 banner = DATA / 'banner.png'
 Image.new('RGBA', (300, 80), (240, 240, 240, 230)).save(banner)
 key = uid()
 db.add(Asset(id=key, kind='banner', name='Баннер бота.png', path=str(banner), sha256=hashlib.sha256(banner.read_bytes()).hexdigest(), media=inspect(banner, 'banner', key), edit={}))
 db.add(Account(platform='youtube', name='Канал YouTube', connected=True, external_id='UC_TEST', method='oauth'))
 db.add(Account(platform='tiktok', name='@tt_new', connected=True, external_id='tt_new', method='browser_login'))
 db.add(Account(platform='tiktok', name='@tt_second', connected=True, external_id='tt_second', method='browser_login'))

@module.app.post('/api/_test/render')
def fake_render():
 with Session.begin() as db:
  for job in db.query(Job).filter(Job.status == 'queued').all():
   variant = db.get(Variant, job.variant_id)
   target = DATA / 'exports' / f'{variant.id}.mp4'
   target.parent.mkdir(parents=True, exist_ok=True)
   shutil.copy(db.get(Asset, variant.asset_id).path, target)
   variant.output, job.progress = str(target), 100
   job.status = 'scheduled' if job.approved else 'ready'
 return {}

@module.app.post('/api/_test/first/{state}')
def fake_first(state: str):
 with Session.begin() as db:
  release = db.query(releases.Release).filter(releases.Release.status == 'started').first()
  for account_id, job_id in release.jobs['first'].items():
   job = db.get(Job, job_id)
   job.status, job.upload_started_at, job.updated = state, now(), now()
 return {}

@module.app.post('/api/_test/tick')
def fake_tick():
 releases.tick()
 return {}

# Test routes must precede the static mount of the built interface.
routes = module.app.router.routes
for route in [r for r in routes if getattr(r, 'path', '').startswith('/api/_test/')]:
 routes.remove(route)
 routes.insert(0, route)
uvicorn.run(module.app, host='127.0.0.1', port=8796, log_level='error')
`;
const log = fs.openSync(path.join(data, 'server.log'), 'a');
const python = path.join(
  root,
  process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python',
);
const server = spawn(python, ['-c', fixture], {
  cwd: root,
  windowsHide: true,
  stdio: ['ignore', log, log],
  env: { ...process.env, SURVIVALFLOW_DATA: data, SURVIVALFLOW_PORT: '8796' },
});
const report = { isolatedData: data, externalRequests: [], checks: [], layouts: [], errors: [] };
const check = (name, condition, details) => {
  report.checks.push({ name, ok: !!condition, details });
  assert(condition, name + (details ? ' ' + JSON.stringify(details) : ''));
};
let browser;
(async () => {
  try {
    for (let i = 0; i < 200; i++) {
      try {
        if ((await fetch(base + '/api/health')).ok) break;
      } catch {}
      await new Promise((r) => setTimeout(r, 150));
    }
    const api = async (url, method = 'GET', body) => {
      const response = await fetch(base + '/api' + url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      return response.json();
    };
    browser = await chromium.launch({
      channel: process.platform === 'win32' ? 'chrome' : undefined,
      headless: true,
    });
    const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
    page.on('pageerror', (e) => report.errors.push(e.message));
    page.on('request', (r) => {
      if (!r.url().startsWith(base) && !/^(data|blob):/.test(r.url()))
        report.externalRequests.push(r.url());
    });
    const shot = async (name) => {
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(150);
      const bounds = await page.evaluate(() => ({
        width: innerWidth,
        height: innerHeight,
        sw: document.documentElement.scrollWidth,
        sh: document.documentElement.scrollHeight,
        clipped: [...document.querySelectorAll('.release-main,.release-side,.e-nav,.e-content')]
          .filter((n) => n.scrollHeight > n.clientHeight + 2)
          .map((n) => n.className),
        actions: document.querySelector('.story-actions')?.getBoundingClientRect().bottom ?? 0,
      }));
      report.layouts.push({ name, ...bounds });
      await page.screenshot({ path: path.join(out, name + '.png') });
      check(
        `${name}: fits 1024×600 without scroll`,
        bounds.sw <= bounds.width + 1 &&
          bounds.sh <= bounds.height + 1 &&
          !bounds.clipped.length &&
          bounds.actions <= bounds.height,
        bounds,
      );
    };
    const state = () => api('/state');
    const nav = (name) => page.getByRole('button', { name, exact: true }).click();

    await page.goto(base);
    await nav('Выпуск');
    await page.getByText('Выпусков пока нет').waitFor();
    await shot('01-empty');

    // Editor → «К публикации»: the current source opens a saved draft.
    await nav('Монтаж');
    await page.locator('.e-source').first().click();
    await page.getByRole('button', { name: /^К публикации/ }).click();
    await page.locator('.release-row').first().waitFor();
    const add = page.getByLabel('Добавить ролик');
    for (let i = 0; i < 2; i++) {
      await add.selectOption({ index: 1 });
      await page.waitForFunction((n) => document.querySelectorAll('.release-row').length === n, i + 2);
    }
    check('three materials in the draft', (await page.locator('.release-row').count()) === 3);
    await page.locator('.release-toolbar select').first().selectOption({ label: 'Баннер бота.png' });
    await page.getByRole('button', { name: 'Применить ко всем', exact: true }).click();
    await page.waitForFunction(async () => {
      const r = await (await fetch('/api/state')).json();
      return r.assets.filter((a) => a.kind === 'video' && a.edit.banner_id).length === 3;
    });
    check('batch banner applied to every material', true);
    await shot('02-step-materials');

    await page.getByRole('button', { name: 'Далее: Каналы и время', exact: true }).click();
    await page.getByText('Канал YouTube').click();
    await page.getByText('@tt_new').click();
    await page.getByLabel('Видимость всех роликов').selectOption('public');
    await page.waitForFunction(async () => {
      const r = await (await fetch('/api/state')).json();
      return r.assets.filter((a) => a.kind === 'video').every((a) => a.edit.visibility === 'public');
    });
    check(
      'TikTok readiness explains the next step',
      await page.getByText('Вход выполнен · готов к первой отправке').first().isVisible(),
    );
    await shot('03-step-channels');

    // Everything survives a reload: draft, step and choices.
    await page.reload();
    await nav('Выпуск');
    await page.getByRole('tab', { name: /Каналы и время/, selected: true }).waitFor();
    const draft = (await api('/releases'))[0];
    check('draft and step survive reload', draft.plan.step === 2 && draft.account_ids.length === 2, draft);
    check('nothing queued before the final step', (await state()).jobs.length === 0);

    await page.getByRole('button', { name: 'Далее: Перед отправкой', exact: true }).click();
    check('final table shows every planned publication', (await page.locator('.release-table tbody tr').count()) === 6);
    await page.getByRole('checkbox', { name: /После успешного первого ролика продолжить остальные 2/ }).check();
    await shot('04-step-check');
    await page.getByRole('button', { name: 'Запустить выпуск', exact: true }).click();
    await page.getByLabel('Первая публикация TikTok').waitFor();
    let jobs = (await state()).jobs;
    const youtube = jobs.filter((j) => j.account.platform === 'youtube');
    const tiktok = jobs.filter((j) => j.account.platform === 'tiktok');
    check(
      'start: YouTube batch authorized, TikTok gets one unauthorized first video',
      youtube.length === 3 &&
        youtube.every((j) => j.auto_publish && j.approved) &&
        tiktok.length === 1 &&
        !tiktok[0].approved,
      jobs.map((j) => [j.account.name, j.status, j.approved]),
    );
    await page.getByRole('button', { name: 'Запустить выпуск' }).waitFor({ state: 'detached' });
    await shot('05-started-preparing');

    await api('/_test/render', 'POST');
    const publish = page.getByRole('button', { name: 'Опубликовать первый ролик', exact: true });
    await publish.waitFor();
    await page.waitForFunction(() => document.querySelector('.story-screen video')?.readyState >= 1);
    check('final MP4 is shown before the first publication', true);
    await shot('06-first-ready');
    await publish.click();
    await page.getByText(/Отправка разрешена/).waitFor();
    jobs = (await state()).jobs;
    const first = jobs.find((j) => j.account.platform === 'tiktok');
    check(
      'first publication authorizes exactly the shown video and resumes the queue',
      first.approved && first.validation_upload && !(await state()).settings.paused,
    );

    await api('/_test/first/unknown', 'POST');
    await page.getByText(/Ответ TikTok потерян/).waitFor();
    check('lost answer offers a check and no plain retry', !(await page.getByRole('button', { name: /^Повторить/ }).count()));
    await shot('07-first-unknown');
    await page.getByLabel('Ссылка на опубликованный ролик').fill('https://www.tiktok.com/@someone/video/7300000000000000011');
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await page.getByText(/другой аккаунт/).first().waitFor();
    check('link from another account is refused', true);
    await page.getByLabel('Ссылка на опубликованный ролик').fill('https://www.tiktok.com/@tt_new/video/7300000000000000011');
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await page.getByText(/TikTok принял ролик/).waitFor();
    await api('/_test/tick', 'POST');
    await page.getByText(/поставлены в автозалив/).waitFor({ timeout: 8000 });
    jobs = (await state()).jobs;
    check(
      'after the confirmed first video the rest of the batch continues by the plan',
      jobs.filter((j) => j.account.platform === 'tiktok' && j.auto_publish && j.approved).length === 2 &&
        (await state()).accounts.find((a) => a.name === '@tt_new').method === 'browser_verified',
    );
    await shot('08-first-confirmed');

    await nav('Очередь');
    await page.getByRole('radio', { name: /Нужны действия/ }).click();
    await shot('09-queue-actions');
    await page.getByRole('radio', { name: /Все/ }).click();
    check(
      'queue rows offer the next action',
      (await page.locator('.e-table').getByRole('button', { name: 'Проверить результат', exact: true }).count()) === 1,
    );
    await shot('10-queue-all');

    await nav('Каналы');
    check('verified TikTok is ready for autopublish', await page.getByText('Готов к автозаливу').first().isVisible());
    await shot('11-channels');
    await page
      .locator('.e-channel', { hasText: '@tt_second' })
      .getByRole('button', { name: 'Подготовить публикацию', exact: true })
      .click();
    await page.getByRole('tab', { name: /Каналы и время/, selected: true }).waitFor();
    const prepared = (await api('/releases')).find((r) => r.status === 'draft');
    const second = (await state()).accounts.find((a) => a.name === '@tt_second');
    check('«Подготовить публикацию» opens a draft for this channel', prepared?.account_ids.includes(second.id), prepared);
    await shot('12-prepare-from-channel');

    check('no page errors', !report.errors.length, report.errors);
    check('no external requests', !report.externalRequests.length, report.externalRequests);
  } catch (e) {
    report.errors.push(String(e));
    throw e;
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
      }),
    );
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
