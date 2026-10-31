// Real unpacked extension in an isolated Chromium profile, with synthetic TikTok only.
const { chromium }=require('../frontend/node_modules/playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'), folder=fs.mkdtempSync(path.join(os.tmpdir(),'sf-extension-check-'));
const base='http://127.0.0.1:8774', out=path.join(root,'docs/validation/browser-extension');
fs.mkdirSync(out,{recursive:true});
const log=fs.openSync(path.join(folder,'server.log'),'a');
const code="from backend import browser_pairing; browser_pairing.open_regular_chrome=lambda:None; import uvicorn; uvicorn.run('backend.app:app',host='127.0.0.1',port=8774)";
const server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-c',code],{cwd:root,env:{...process.env,SURVIVALFLOW_DATA:folder,SURVIVALFLOW_PORT:'8774'},windowsHide:true,stdio:['ignore',log,log]});
fs.closeSync(log);
(async()=>{
 let context;
 const errors=[];
 try {
  let healthy=false;
  for(let n=0;n<100;n++){try{const h=await(await fetch(base+'/api/health')).json();assert.equal(path.resolve(h.data_directory),folder);healthy=true;break;}catch{await new Promise(r=>setTimeout(r,100));}}
  assert(healthy);
  const post=async(url,body)=>{const r=await fetch(base+'/api'+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.equal(r.status,200,await r.clone().text());return r.json();};
  const account=await post('/accounts',{platform:'tiktok',name:'Мой Chrome'});
  await post(`/accounts/${account.id}/connect`);
  const details=await(await fetch(base+`/api/accounts/${account.id}/browser-pairing`)).json();
  const extension=path.join(root,'browser-extension');
  context=await chromium.launchPersistentContext(path.join(folder,'chromium-profile'),{channel:'chromium',headless:true,viewport:{width:1024,height:600},args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
  context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
  const manager=await context.newPage();
  await manager.goto('chrome://extensions');
  const item=manager.locator('extensions-item').filter({hasText:'FrameFlow'});
  await item.waitFor();
  const extensionId=await item.getAttribute('id');
  assert(/^[a-p]{32}$/.test(extensionId));
  await context.route('https://www.tiktok.com/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Синтетический TikTok</title><a data-e2e="nav-profile" href="/@cartoon.chrome">Мой профиль</a>'}));
  await context.addCookies([
   {name:'sessionid',value:'SYNTHETIC_TIKTOK_SESSION',domain:'.tiktok.com',path:'/',secure:true,httpOnly:true,sameSite:'Lax'},
   {name:'private_google',value:'SYNTHETIC_GOOGLE_MUST_NOT_BE_READ',domain:'.google.com',path:'/',secure:true,httpOnly:true},
  ]);
  const tiktok=await context.newPage(); await tiktok.goto('https://www.tiktok.com/@cartoon.chrome');
  const app=await context.newPage(); await app.goto(base);
  await app.getByRole('button',{name:'Каналы',exact:true}).click();
  await app.getByRole('button',{name:'Подключение',exact:true}).click();
  await app.getByLabel('Код подключения Chrome',{exact:true}).waitFor();
  await app.waitForFunction(()=>document.querySelector('dialog input[readonly]')?.value.length===32);
  assert.equal(await app.getByLabel('Код подключения Chrome',{exact:true}).inputValue(),details.code);
  const bounds=await app.locator('dialog').evaluate(e=>({h:innerHeight,top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,scroll:e.scrollHeight>e.clientHeight+2}));
  assert(bounds.top>=0&&bounds.bottom<=bounds.h&&!bounds.scroll);
  await app.screenshot({path:path.join(out,'app-pairing-code.png')});
  const popup=await context.newPage(); await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup.locator('#code').fill(details.code);
  await popup.locator('summary').click();
  await popup.locator('#port').fill('8774');
  const google=await popup.evaluate(async()=>{try{return await chrome.cookies.getAll({domain:'google.com'});}catch{return [];}});
  assert.deepEqual(google,[],'extension has no Google cookie permission');
  const manifest=await popup.evaluate(()=>chrome.runtime.getManifest());
  assert.deepEqual(manifest.permissions,['cookies','scripting']);
  assert.deepEqual(manifest.host_permissions,['https://*.tiktok.com/*','http://127.0.0.1/*']);
  await tiktok.bringToFront();
  // The popup is opened as a background page for testing. Keep TikTok the active
  // tab, exactly as it is when the user opens a real Chrome action popup.
  await popup.evaluate(()=>document.querySelector('#connect').click());
  await popup.waitForFunction(()=>document.querySelector('#status').textContent.includes('Вернитесь в FrameFlow')||document.querySelector('#status').dataset.error==='true');
  assert.equal(await popup.locator('#status').getAttribute('data-error'),'false',await popup.locator('#status').innerText());
  assert((await popup.locator('#status').innerText()).includes('@cartoon.chrome'));
  assert.equal(await popup.locator('#code').inputValue(),'');
  await popup.screenshot({path:path.join(out,'extension-confirmation.png')});
  let state=await(await fetch(base+'/api/state')).json();
  assert.equal(state.accounts[0].connected,false,'user confirmation still required');
  await app.getByRole('button',{name:'Сохранить @cartoon.chrome',exact:true}).waitFor();
  await app.getByRole('button',{name:'Сохранить @cartoon.chrome',exact:true}).click();
  await app.getByText('Подключён: @cartoon.chrome · cartoon.chrome',{exact:true}).waitFor();
  state=await(await fetch(base+'/api/state')).json();
  assert.equal(state.accounts[0].connected,true);
  assert.equal(state.accounts[0].method,'browser_login');
  assert.equal(state.jobs.length,0);
  assert(!JSON.stringify(state).includes('SYNTHETIC_TIKTOK_SESSION'));
  const secrets=fs.readdirSync(path.join(folder,'secrets')).filter(n=>n.endsWith('.dpapi'));
  assert.equal(secrets.length,1);
  assert(!fs.readFileSync(path.join(folder,'secrets',secrets[0])).includes(Buffer.from('SYNTHETIC_TIKTOK_SESSION')));
  assert.deepEqual(errors,[]);
  const report={unpackedExtension:'passed',realChromeApis:'passed',webCryptoToPythonHmac:'passed',explicitConfirmation:'passed',encryptedSession:'passed',noGooglePermission:true,noPublications:true,syntheticTikTok:true,uiBounds:bounds,errors};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(report,null,2)); console.log(JSON.stringify(report));
 }finally{
  if(context)await context.close();
  if(server.exitCode===null)execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
