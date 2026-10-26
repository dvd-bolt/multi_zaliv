// UI checks use simulated auth transitions; no TikTok credentials or remote login.
const { chromium } = require('../frontend/node_modules/playwright');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const folder=fs.mkdtempSync(path.join(os.tmpdir(),'survivalflow-tiktok-ui-'));
const out=path.join(root,'docs/validation/tiktok-login');
fs.mkdirSync(out,{recursive:true});
const base='http://127.0.0.1:8773';
const log=fs.openSync(path.join(folder,'server.log'),'a');
const server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8773'],{cwd:root,env:{...process.env,SURVIVALFLOW_DATA:folder,SURVIVALFLOW_PORT:'8773'},windowsHide:true,stdio:['ignore',log,log]});
fs.closeSync(log);
(async()=>{
 let browser;
 const errors=[],checks=[],requests=[];
 try {
  let healthy=false;
  for(let i=0;i<100;i++) {
   try {const health=await (await fetch(base+'/api/health')).json(); assert.equal(path.resolve(health.data_directory),folder); healthy=true;break;} catch {await new Promise(r=>setTimeout(r,100));}
  }
  assert(healthy,'isolated API not ready');
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1024,height:600}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400)errors.push(`${r.status()} ${r.url()}`);});
  let key='',phase='';
  await page.route('**/api/state',async route=>{
   const response=await route.fetch(); const data=await response.json();
   for(const account of data.accounts)if(account.id===key&&phase) {
    account.auth=phase;
    if(phase==='connected'){account.connected=true;account.method='browser_login';account.external_id='cartoon.channel';account.name='@cartoon.channel';}
   }
   await route.fulfill({response,json:data});
  });
  await page.route('**/api/accounts/*/browser-pairing',route=>route.fulfill({json:{code:'A'.repeat(32),expires_at:Date.now()/1000+600}}));
  await page.route('**/api/accounts/*/connect**',async route=>{
   const request=route.request(); const pathname=new URL(request.url()).pathname;
   requests.push({path:pathname,body:request.postDataJSON()});
   if(pathname.endsWith('/cancel'))phase='disconnected';
   else if(pathname.endsWith('/confirm')){assert.equal(request.postDataJSON().handle,'cartoon.channel');phase='verifying';}
   else phase=pathname.endsWith('/return-to-tiktok')?'waiting':'browser_waiting';
   await route.fulfill({json:{status:phase}});
  });
  const snapshot=async name=>{
   await page.evaluate(()=>document.fonts.ready);
   const bounds=await page.evaluate(()=>({w:innerWidth,h:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight,dialogs:[...document.querySelectorAll('dialog[open]')].map(e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,sh:e.scrollHeight,ch:e.clientHeight};})}));
   assert.equal(bounds.w,bounds.sw);assert.equal(bounds.h,bounds.sh);
   for(const dialog of bounds.dialogs){assert(dialog.top>=0&&dialog.bottom<=bounds.h&&dialog.left>=0&&dialog.right<=bounds.w);assert(dialog.sh<=dialog.ch+2);}
   await page.screenshot({path:path.join(out,name+'.png')}); checks.push({name,...bounds});
  };
  await page.goto(base);
  await page.getByRole('button',{name:'Каналы',exact:true}).click();
  await page.getByRole('button',{name:'Добавить аккаунт',exact:true}).click();
  await page.getByLabel('Платформа',{exact:true}).selectOption('tiktok');
  await page.getByLabel('Название для вас',{exact:true}).fill('Мультики');
  await page.getByRole('button',{name:'Добавить',exact:true}).click();
  await page.locator('dialog').waitFor({state:'hidden'});
  key=(await (await page.request.get(base+'/api/state')).json()).accounts[0].id;
  await page.getByRole('button',{name:'Подключение',exact:true}).click();
  await page.getByRole('button',{name:'Открыть TikTok в моём Chrome',exact:true}).waitFor();
  assert.equal(await page.locator('details').evaluate(e=>e.open),false);
  await snapshot('tiktok-before-login');
  await page.getByRole('button',{name:'Открыть TikTok в моём Chrome',exact:true}).click();
  await page.getByLabel('Код подключения Chrome',{exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('dialog input[readonly]')?.value.length===32);
  await snapshot('tiktok-waiting');
  phase='action:google';
  await page.getByRole('button',{name:'Вернуться к входу в TikTok',exact:true}).waitFor();
  await snapshot('tiktok-google-rejected');
  await page.getByRole('button',{name:'Вернуться к входу в TikTok',exact:true}).click();
  await page.getByRole('button',{name:'Ожидаем вход…',exact:true}).waitFor();
  await page.getByRole('button',{name:'Отменить вход',exact:true}).click();
  await page.getByRole('button',{name:'Открыть TikTok в моём Chrome',exact:true}).waitFor();
  await snapshot('tiktok-cancelled');
  await page.getByRole('button',{name:'Открыть TikTok в моём Chrome',exact:true}).click();
  phase='confirm:@cartoon.channel';
  await page.getByRole('button',{name:'Сохранить @cartoon.channel',exact:true}).waitFor();
  await snapshot('tiktok-confirm');
  await page.getByRole('button',{name:'Сохранить @cartoon.channel',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Проверяем аккаунт и сохраняем сессию'}).waitFor();
  await snapshot('tiktok-verifying');
  phase='connected';
  await page.getByRole('button',{name:'Войти заново в TikTok',exact:true}).waitFor();
  await snapshot('tiktok-connected');
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.getByText('Вход выполнен',{exact:true}).waitFor();
  await snapshot('tiktok-channel-card');
  await page.getByRole('button',{name:'Подключение',exact:true}).click();
  phase='error: Окно входа закрыто. Повторите вход.';
  await page.getByRole('alert').filter({hasText:'Окно входа закрыто'}).waitFor();
  await snapshot('tiktok-error');
  await page.setViewportSize({width:1366,height:600});
  await snapshot('tiktok-error-wide');
  assert.deepEqual(errors,[]);
  assert.equal(requests.filter(r=>r.path.endsWith('/confirm')).length,1);
  fs.writeFileSync(path.join(out,'ui-results.json'),JSON.stringify({simulatedAuth:true,noRealAccounts:true,checks,requests,errors},null,2));
  console.log(JSON.stringify({checks:checks.length,errors,simulatedAuth:true}));
 } finally {
  if(browser)await browser.close();
  if(server.exitCode===null)execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
