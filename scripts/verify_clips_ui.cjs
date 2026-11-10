// Disposable API and synthetic video/captions; real FFmpeg, no publishers or external API calls.
const { chromium } = require('../frontend/node_modules/playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const data = fs.mkdtempSync(path.join(os.tmpdir(), 'frameflow-clips-ui-'));
const out = path.resolve(process.env.FRAMEFLOW_VALIDATION_OUT || path.join(root, 'docs/validation/clips'));
fs.mkdirSync(out, { recursive: true });
const base = 'http://127.0.0.1:8795';
const fixture = `
import json, threading, time
from pathlib import Path
import uvicorn
import backend.app as module
from backend import clips
from backend.config import DATA, ROOT, binary
from backend.db import Session, Setting
from backend.media import command
assert DATA != ROOT / 'data'
module.ensure_queue_worker = lambda: None
with Session.begin() as db:
 db.get(Setting, 'application').value = {'paused': False, 'buba_required': False}
video = DATA / 'synthetic.mp4'
command([binary('ffmpeg'), '-v','error','-y','-f','lavfi','-i','testsrc2=size=160x90:rate=5:duration=75',
 '-f','lavfi','-i','sine=frequency=300:duration=75','-c:v','libx264','-c:a','aac','-shortest',str(video)])
state = clips.new_state('Синтетический подкаст — проверка нарезок', path=str(video))
state.update(status='ready', duration=75, detail='Контрольные моменты', method='Синтетическая расшифровка')
(clips.root()/state['id']/'transcript.json').write_text(json.dumps(
 [{'start': i*.5, 'end': i*.5+.4, 'text':'Проверка.'} for i in range(150)]), encoding='utf-8')
state['candidates'] = [{'id':str(i), 'start':i*10., 'end':i*10.+12., 'title':f'Момент {i+1}',
 'reason':'Контрольная фраза', 'selected':False, 'status':'new'} for i in range(6)]
clips.save(state)
def work():
 while True:
  if any(json.loads(p.read_text(encoding='utf-8'))['status']=='queued_render' for p in clips.root().glob('*/state.json')):
   clips.process_one()
  (DATA/'worker.heartbeat').touch()
  time.sleep(.2)
threading.Thread(target=work, daemon=True).start()
uvicorn.run(module.app, host='127.0.0.1', port=8795, log_level='error')
`;
const log = fs.openSync(path.join(data, 'server.log'), 'a');
const server = spawn(path.join(root, '.venv/Scripts/python.exe'), ['-c', fixture], {
  cwd: root, windowsHide: true, stdio: ['ignore', log, log],
  env: { ...process.env, SURVIVALFLOW_DATA: data, SURVIVALFLOW_PORT: '8795' },
});
const report = { isolatedData: data, syntheticTranscript: true, externalRequests: [], checks: [], layouts: [], errors: [] };
let browser;
(async () => {
  try {
    for (let i = 0; i < 150; i++) {
      try { if ((await fetch(base + '/api/health')).ok) break; } catch {}
      await new Promise(r => setTimeout(r, 200));
    }
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1024, height: 600 } });
    page.on('pageerror', e => report.errors.push(e.message));
    page.on('request', r => {
      if (!r.url().startsWith(base) && !/^(data|blob):/.test(r.url())) report.externalRequests.push(r.url());
    });
    await page.goto(base);
    await page.getByRole('button', { name: 'Нарезки', exact: true }).click();
    await page.getByLabel('Заголовок клипа').first().waitFor();
    assert.equal(await page.getByLabel('Заголовок клипа').count(), 4);
    await page.getByRole('button', { name: 'Далее', exact: true }).click();
    assert.equal(await page.getByLabel('Заголовок клипа').count(), 2);
    await page.getByRole('button', { name: 'Назад', exact: true }).click();
    report.checks.push('Six moments paginate four at a time');
    for (const [width, height] of [[1024, 600], [1440, 900]]) {
      await page.setViewportSize({ width, height });
      const bounds = await page.locator('.clips-view').evaluate(e => ({
        width: innerWidth, height: innerHeight, sw: document.documentElement.scrollWidth,
        sh: document.documentElement.scrollHeight,
        actionBottom: e.querySelector('.story-actions').getBoundingClientRect().bottom,
        clipped: [...e.querySelectorAll('.story-panel,.stories-list,.story-workspace')]
          .filter(n => n.scrollHeight > n.clientHeight + 2).map(n => n.className),
      }));
      report.layouts.push(bounds);
      await page.screenshot({ path: path.join(out, `clips-${width}.png`) });
      assert(bounds.sw <= width && bounds.sh <= height && bounds.actionBottom <= height && !bounds.clipped.length, JSON.stringify(bounds));
    }
    await page.setViewportSize({ width: 1024, height: 600 });
    await page.getByLabel('Заголовок клипа').first().fill('Проверенный момент');
    await page.getByLabel('Взять момент', { exact: true }).first().check();
    await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Сохранить' && b.disabled));
    await page.reload();
    await page.getByRole('button', { name: 'Нарезки', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Заголовок клипа"]')?.value === 'Проверенный момент');
    assert(await page.getByLabel('Взять момент', { exact: true }).first().isChecked());
    report.checks.push('Edited title and selection survive reload');
    await page.getByRole('button', { name: 'Собрать 1', exact: true }).click();
    for (let i = 0; i < 240; i++) {
      const state = await (await page.request.get(base + '/api/clips')).json();
      assert.notEqual(state.sources[0].status, 'failed', state.sources[0].error);
      if (state.sources[0].candidates[0].has_output) break;
      await new Promise(r => setTimeout(r, 500));
    }
    await page.getByRole('button', { name: 'В монтажную', exact: true }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'В монтажную' && !b.disabled));
    await page.getByTitle('Смотреть собранный клип').first().click();
    await page.waitForFunction(() => document.querySelector('.story-screen video')?.readyState >= 2);
    const info = await page.locator('.story-screen video').evaluate(v => ({ width: v.videoWidth, height: v.videoHeight, duration: v.duration }));
    assert.equal(info.width, 1080); assert.equal(info.height, 1920); assert(Math.abs(info.duration - 12) < .5);
    report.checks.push('Selected clip renders to real 1080x1920 MP4 and plays');
    await page.screenshot({ path: path.join(out, 'clips-rendered.png') });
    const before = await (await page.request.get(base + '/api/state')).json();
    await page.getByRole('button', { name: 'В монтажную', exact: true }).click();
    await page.locator('.e-source.active').waitFor();
    const after = await (await page.request.get(base + '/api/state')).json();
    assert.equal(after.jobs.length, before.jobs.length);
    assert(after.assets.some(a => a.edit.title === 'Проверенный момент'));
    report.checks.push('Transfer creates an editor source without publication jobs');
    const failed = await (await page.request.post(base + '/api/clips/link', {
      data: { url: 'https://www.youtube.com/watch?v=test1234567' },
    })).json();
    const statePath = path.join(data, 'clips', failed.id, 'state.json');
    const failedState = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    failedState.status = 'failed'; failedState.error = 'Временная ошибка загрузки';
    fs.writeFileSync(statePath, JSON.stringify(failedState));
    await page.reload();
    await page.getByRole('button', { name: 'Нарезки', exact: true }).click();
    const retry = page.getByRole('button', { name: 'Повторить загрузку', exact: true });
    await retry.waitFor(); assert(await retry.isEnabled());
    const retryBounds = await page.locator('.story-actions').boundingBox();
    assert(retryBounds.y + retryBounds.height <= 600);
    await page.screenshot({ path: path.join(out, 'clips-download-retry.png') });
    const retryResponse = page.waitForResponse(r => r.url().endsWith(`/${failed.id}/analyze`));
    await retry.click();
    const resumed = await (await retryResponse).json();
    assert.equal(resumed.id, failed.id); assert.equal(resumed.status, 'queued_download');
    await page.request.post(base + `/api/clips/${failed.id}/cancel`);
    report.checks.push('Failed link can retry from the same source, without a duplicate or external request');
    assert.deepEqual(report.errors, []); assert.deepEqual(report.externalRequests, []);
  } catch (e) { report.errors.push(String(e)); throw e; }
  finally {
    await browser?.close(); server.kill(); fs.closeSync(log);
    fs.writeFileSync(path.join(out, 'ui-results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
