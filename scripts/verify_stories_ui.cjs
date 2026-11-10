const {chromium}=require('../frontend/node_modules/playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),data=fs.mkdtempSync(path.join(os.tmpdir(),'frameflow-story-ui-'));
const out=path.join(root,'docs/validation/averages-stories');fs.mkdirSync(out,{recursive:true});
const log=fs.openSync(path.join(data,'server.log'),'a');
const server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-m','scripts.story_ui_fixture'],{cwd:root,windowsHide:true,stdio:['ignore',log,log],env:{...process.env,SURVIVALFLOW_DATA:data,SURVIVALFLOW_PORT:'8794'}});
const base='http://127.0.0.1:8794';
(async()=>{let browser;const errors=[],checks=[],layouts=[];
try{
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,300));}
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1024,height:600}});page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await page.getByRole('button',{name:'Статистика',exact:true}).click();await page.getByRole('tab',{name:'Средние',exact:true}).click();
 await page.getByRole('heading',{name:'В среднем на один ролик'}).waitFor();
 assert((await page.locator('.st-average-table tbody tr').first().innerText()).includes('2 000'));
 await page.screenshot({path:path.join(out,'averages-1024.png')});checks.push('Averages use controlled observations and show n');
 await page.getByRole('button',{name:'TikTok',exact:true}).click();await page.getByText('Роликов: 0',{exact:true}).waitFor();checks.push('Platforms remain separate');
 await page.getByRole('button',{name:'Истории',exact:true}).click();await page.getByLabel('Название истории').waitFor();
 for(const [width,height] of [[1024,600],[1440,900]]){
  await page.setViewportSize({width,height});
  for(const tab of ['Сценарий','Голос','Фон','Субтитры','Выпуск']){
   await page.getByRole('tab',{name:tab,exact:true}).click();
   const geometry=await page.evaluate(()=>{const panel=document.querySelector('.story-panel'),actions=document.querySelector('.story-actions');return {w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight,panel:panel.getBoundingClientRect().toJSON(),actions:actions.getBoundingClientRect().toJSON(),children:[...panel.children].map(e=>({tag:e.tagName,bottom:e.getBoundingClientRect().bottom}))};});
   layouts.push({width,height,tab,...geometry});assert(geometry.w<=width&&geometry.h<=height,'Desktop root overflow');
   const overlaps=geometry.children.filter(c=>c.bottom>geometry.actions.y+1);if(overlaps.length)errors.push('Panel overlaps actions: '+width+' '+tab+' '+JSON.stringify(overlaps));
   await page.screenshot({path:path.join(out,`stories-${width}-${tab}.png`)});
  }
 }
 await page.setViewportSize({width:1024,height:600});
 await page.getByRole('tab',{name:'Субтитры',exact:true}).click();
 await page.getByRole('button',{name:'Распознать локально',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.story-status')?.textContent.includes('Готово.'),{timeout:120000});
 await page.getByRole('checkbox',{name:'Текст и тайминги проверены',exact:true}).check();
 await page.getByRole('button',{name:'Сохранить SRT',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('.story-status')?.textContent.includes('В очереди'));
 await page.getByRole('tab',{name:'Выпуск',exact:true}).click();
 checks.push('Actual local speech recognition, observed timing, editable SRT and review gate');
 // Build actual speech + actual FFmpeg, on this disposable DB. No platform requests.
 await page.getByLabel('Область обработки').selectOption('all');
 const renderResponse=page.waitForResponse(r=>r.url().endsWith('/tasks')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Собрать MP4',exact:true}).click();
 const renderTask=await(await renderResponse).json();
 let rendered=false;
 for(let i=0;i<360;i++){
  const current=await(await page.request.get(base+'/api/stories')).json(),t=current.tasks.find(t=>t.id===renderTask.id);
  assert(t?.status!=='failed',t?.error);
  if(t?.status==='succeeded'){assert(current.stories[0].parts.every(p=>p.has_output));rendered=true;break;}
  await new Promise(r=>setTimeout(r,500));
 }
 assert(rendered,'Both MP4 parts must complete before acceptance');
 await page.waitForFunction(()=>document.querySelector('.story-status')?.textContent.includes('Готово.'));
 await page.locator('.story-screen video').waitFor();
 await page.locator('.story-screen video').evaluate(async v=>{if(v.readyState<2)await new Promise(r=>v.addEventListener('loadeddata',r,{once:true}));v.currentTime=1;await new Promise(r=>v.addEventListener('seeked',r,{once:true}));});
 checks.push('Two actual speech parts rendered by the durable story worker');
 await page.screenshot({path:path.join(out,'story-rendered-1024.png')});
 const catalog=await(await page.request.get(base+'/api/stories')).json();
 fs.writeFileSync(path.join(out,'rendered-artifacts.json'),JSON.stringify({storyId:catalog.stories[0].id,parts:catalog.stories[0].parts.map(p=>({id:p.id,duration:p.duration,hasOutput:p.has_output,cues:p.cues,background:p.background})),isolatedData:data},null,2));
 const state=await(await page.request.get(base+'/api/state')).json();const before=state.jobs.length;
 await page.getByRole('button',{name:'Передать в монтажную',exact:true}).click();await page.getByText('Баннер',{exact:true}).first().waitFor();
 const after=await(await page.request.get(base+'/api/state')).json();assert.equal(after.jobs.length,before);assert(after.assets.some(a=>a.edit.genre==='story'));checks.push('Transfer to existing editor creates an asset and no publication');
 await page.getByRole('button',{name:'Истории',exact:true}).click();await page.getByRole('tab',{name:'Сценарий',exact:true}).click();await page.getByRole('button',{name:'Импорт сценария',exact:true}).click();
 await page.getByLabel('Полный сценарий').fill('Название: Проверка\nЧастей: 2\nФон: Мойка машин\n[Часть 1]\nПервый текст.\n[Часть 2]\nВторой текст.');
 await page.getByRole('button',{name:'Разобрать по частям',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});assert.equal(await page.getByLabel('Текст части').inputValue(),'Первый текст.');await page.getByRole('button',{name:'Сохранить',exact:true}).click();checks.push('Script import separates metadata and persists two parts');
 assert.deepEqual(errors,[]);
}finally{fs.writeFileSync(path.join(out,'ui-results.json'),JSON.stringify({isolatedData:data,realTtsRequestsInUi:0,checks,layouts,errors},null,2));if(browser)await browser.close();server.kill();fs.closeSync(log);}
console.log(JSON.stringify({checks,errors}));})().catch(e=>{console.error(e);process.exitCode=1});
