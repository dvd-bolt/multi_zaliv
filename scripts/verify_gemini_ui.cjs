const { chromium } = require('../frontend/node_modules/playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..'), tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sf-gemini-'));
const out = path.join(root, 'docs/validation/gemini'); fs.mkdirSync(out, { recursive: true });
const server = spawn(path.join(root, '.venv/Scripts/python.exe'), ['-m', 'uvicorn', 'backend.app:app', '--port', '8781', '--host', '127.0.0.1'], { cwd: root, windowsHide: true, stdio: 'ignore', env: { ...process.env, SURVIVALFLOW_DATA: tmp, SURVIVALFLOW_PORT: '8781' } });
(async () => {
  let browser;
  try {
    for (let i = 0; i < 100; i++) { try { if ((await fetch('http://127.0.0.1:8781/api/health')).ok) break; } catch {} await new Promise(r => setTimeout(r, 100)); }
    const ff = spawn(path.join(root, 'data/tools/ffmpeg.exe'), ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:size=90x160:rate=2:duration=3', '-c:v', 'libx264', path.join(tmp, 'source.mp4')], { windowsHide: true, stdio: 'ignore' });
    await new Promise((yes, no) => ff.on('exit', code => code ? no(Error('ffmpeg')) : yes()));
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const p = await browser.newPage({ viewport: { width: 1024, height: 600 } });
    const errors = []; p.on('pageerror', e => errors.push(e.message));
    let requests = 0, finish = true, failure = false;
    const failureDetail = 'Все модели Gemini сейчас недоступны. 3.8-flash: тайм-аут; 3.6-flash: HTTP 503; 3.5-flash-lite: квота (429); 3.1-flash-lite: HTTP 503';
    const proposal = { title: 'Синтетический пример', description: 'Синий экран для проверки интерфейса.', hashtags: ['#тест', '#пример'], uncertain: true, model: 'gemini-3.6-flash' };
    await p.route('**/api/ai', r => r.fulfill({ json: { configured: true, auto: true, models: ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite'] } }));
    await p.route('**/api/assets/*/ai-caption', r => {
      if (r.request().method() === 'POST') { requests++; return r.fulfill({ json: { state: 'running' } }); }
      return r.fulfill({ json: failure ? { state: 'error', detail: failureDetail } : finish ? { state: 'ready', proposal } : { state: 'running', detail: 'Gemini 3.6-flash · анализ исходника…' } });
    });
    await p.goto('http://127.0.0.1:8781');
    await p.locator('input[accept="video/*,.mkv"]').setInputFiles(path.join(tmp, 'source.mp4'));
    await p.getByRole('tab', { name: 'Публикация', exact: true }).waitFor();
    let liveAsset;
    for (let i = 0; i < 100 && !liveAsset; i++) {
      const liveState = await (await fetch('http://127.0.0.1:8781/api/state')).json();
      liveAsset = liveState.assets.find(a => a.kind === 'video');
      if (!liveAsset) await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert(liveAsset, 'Imported asset must be committed before checking its status');
    const livePoll = await fetch(`http://127.0.0.1:8781/api/assets/${liveAsset.id}/ai-caption`);
    assert.equal(livePoll.status, 200);
    assert.equal((await livePoll.json()).state, 'idle');
    await p.getByRole('tab', { name: 'Публикация', exact: true }).click();
    await p.waitForFunction(() => document.querySelector('textarea')?.value.includes('Синий экран'));
    assert.equal(requests, 1);
    assert((await p.getByLabel('Описание', { exact: true }).inputValue()).includes('#бубавпн'));
    const geometry = await p.evaluate(() => {
      const i = document.querySelector('[data-inspector]'), save = document.querySelector('.e-savebar');
      return { pageHeight: document.documentElement.scrollHeight, viewport: innerHeight, contentBottom: Math.max(...[...i.children].map(e => e.getBoundingClientRect().bottom)), saveTop: save.getBoundingClientRect().top };
    });
    await p.screenshot({ path: path.join(out, 'caption.png') });
    assert(geometry.pageHeight <= geometry.viewport && geometry.contentBottom <= geometry.saveTop, JSON.stringify(geometry));
    finish = false;
    await p.getByRole('button', { name: 'ИИ: описание', exact: true }).click();
    await p.getByLabel('Описание', { exact: true }).fill('Ручной текст пользователя');
    finish = true;
    await p.getByRole('button', { name: 'Применить', exact: true }).waitFor();
    assert.equal(await p.getByLabel('Описание', { exact: true }).inputValue(), 'Ручной текст пользователя');
    failure = true;
    await p.getByRole('button', { name: 'ИИ: описание', exact: true }).click();
    await p.getByRole('button', { name: 'Ошибка ИИ · показать причину', exact: true }).click();
    await p.getByText(failureDetail, { exact: true }).waitFor();
    assert.equal(await p.getByLabel('Описание', { exact: true }).inputValue(), 'Ручной текст пользователя');
    const errorBounds = await p.getByRole('dialog').evaluate(e => ({ top: e.getBoundingClientRect().top, bottom: e.getBoundingClientRect().bottom, scroll: e.scrollHeight > e.clientHeight }));
    assert(errorBounds.top >= 0 && errorBounds.bottom <= 600 && !errorBounds.scroll);
    await p.screenshot({ path: path.join(out, 'error-details.png') });
    await p.getByRole('button', { name: 'Закрыть диалог', exact: true }).click();
    await p.getByRole('button', { name: 'Настройки Gemini', exact: true }).click();
    const dialog = p.getByRole('dialog');
    await p.getByText('Автоописание для исходника без текста', { exact: true }).waitFor();
    const bounds = await dialog.evaluate(e => ({ top: e.getBoundingClientRect().top, bottom: e.getBoundingClientRect().bottom, scroll: e.scrollHeight > e.clientHeight }));
    await p.screenshot({ path: path.join(out, 'settings.png') });
    assert(bounds.top >= 0 && bounds.bottom <= 600 && !bounds.scroll, JSON.stringify(bounds));
    assert.equal(await p.getByLabel('API-ключ Gemini').inputValue(), '');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(out, 'ui-results.json'), JSON.stringify({ synthetic: true, noRealGoogleRequests: true, requests, geometry, bounds, errorBounds, errors, manualTextPreserved: true }, null, 2));
    console.log(JSON.stringify({ requests, geometry, bounds, errors }));
  } finally { await browser?.close(); server.kill(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
