const {chromium}=require('../frontend/node_modules/playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'sf-auto-')),out=path.join(root,'docs/validation/autopublish');fs.mkdirSync(out,{recursive:true});
const fixture=String.raw`
import importlib,threading,time,uvicorn,shutil
from datetime import UTC,datetime,timedelta
from sqlalchemy import select
from backend.app import app
from backend.db import Session,Account,Setting,Asset,Job,settings,uid
from backend.schemas import Edit
from backend.config import DATA
from backend import platforms,worker
module=importlib.import_module('backend.app')
module.ensure_queue_worker=lambda:None
writes=[]
class Service:
 def channels(self):return self
 def videos(self):return self
 def list(self,**kw):return self
 def execute(self):return {'items':[{'id':'UC_SYNTHETIC'}]}
 def insert(self,**kw):writes.append(kw['body']);return self
 def next_chunk(self,**kw):return None,{'id':'SYNTHVIDEO1'}
platforms.youtube_service=lambda account:Service()
with Session.begin() as db:
 db.add(Account(id='synthetic',platform='youtube',name='TEST ONLY channel',external_id='UC_SYNTHETIC',connected=True,method='oauth'))
 db.get(Setting,'application').value={**settings(db),'buba_required':False,'paused':True,'interval':0}
@app.get('/api/test/deliveries')
def deliveries():return {'count':len(writes)}
@app.post('/api/test/release')
def release():
 with Session.begin() as db:
  for job in db.scalars(select(Job).where(Job.status=='scheduled')):job.scheduled=(datetime.now(UTC)-timedelta(seconds=1)).isoformat()
 return {'ok':True}
@app.post('/api/test/buba')
def buba():
 with Session.begin() as db:
  a=db.scalar(select(Asset).where(Asset.kind=='video'));a.edit=Edit.model_validate({**a.edit,'buba_mode':True,'buba_review':{}}).model_dump()
 return {'ok':True}
@app.post('/api/test/bulk')
def bulk():
 with Session.begin() as db:
  ids=[]
  for i in range(6):
   account=Account(id=uid(),platform='youtube' if i<3 else 'tiktok',name=('Long channel name '+str(i)+' ')+'x'*55,external_id='UC_FAKE'+str(i),connected=True,method='oauth' if i<3 else 'browser_verified');db.add(account);ids.append(account.id)
  a=db.scalar(select(Asset).where(Asset.kind=='video'))
  a.name='Very long source name '+'x'*180+'.mp4'
  a.edit={**a.edit,'targets':ids}
  for i in range(2):
   new_id=uid();db.add(Asset(id=new_id,kind='video',name=str(i)+' '+a.name,path=a.path,sha256=a.sha256,media=a.media,edit={**a.edit,'title':'Synthetic '+str(i)}));shutil.copy2(DATA/'thumbnails'/f'{a.id}.png',DATA/'thumbnails'/f'{new_id}.png')
 return {'ok':True}
@app.post('/api/test/many')
def many():
 with Session.begin() as db:
  a=db.scalar(select(Asset).where(Asset.kind=='video'))
  for i in range(27):
   new_id=uid();db.add(Asset(id=new_id,kind='video',name=str(i)+' '+a.name,path=a.path,sha256=a.sha256,media=a.media,edit={**a.edit,'title':'Synthetic extra '+str(i)}));shutil.copy2(DATA/'thumbnails'/f'{a.id}.png',DATA/'thumbnails'/f'{new_id}.png')
 return {'ok':True}
def loop():
 while True:
  worker.tick();time.sleep(.25)
threading.Thread(target=loop,daemon=True).start()
from starlette.routing import Mount
app.router.routes.sort(key=lambda route:isinstance(route,Mount))
uvicorn.run(app,host='127.0.0.1',port=8782,log_level='error')
`;
const server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-c',fixture],{cwd:root,env:{...process.env,SURVIVALFLOW_DATA:tmp,SURVIVALFLOW_PORT:'8782'},windowsHide:true,stdio:'ignore'});
const base='http://127.0.0.1:8782';
async function api(url,method='GET',body){const r=await fetch(base+'/api'+url,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.json()}
async function wait(check){for(let i=0;i<120;i++){const state=await api('/state');if(check(state))return state;await new Promise(r=>setTimeout(r,200))}throw Error('state timeout')}
(async()=>{let browser;try{
for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const source=path.join(tmp,'auto-source.mp4');const ff=spawn(path.join(root,'data/tools/ffmpeg.exe'),['-v','error','-y','-f','lavfi','-i','color=blue:size=90x160:rate=5:duration=3','-c:v','libx264',source],{windowsHide:true,stdio:'ignore'});await new Promise((yes,no)=>ff.on('exit',c=>c?no(Error('ffmpeg')):yes()));
const form=new FormData();form.append('file',new Blob([fs.readFileSync(source)]),'auto-source.mp4');const imported=await(await fetch(base+'/api/assets?kind=video',{method:'POST',body:form})).json();await api(`/assets/${imported.id}/edit`,'PUT',{...imported.edit,buba_mode:false,format:'720',duration:.5,title:'Synthetic scheduled video',caption:'Synthetic example',visibility:'public',targets:['synthetic']});
browser=await chromium.launch({channel:'chrome',headless:true});const p=await browser.newPage({viewport:{width:1024,height:600}}),errors=[];let expected400=false;p.on('pageerror',e=>errors.push(e.message));p.on('response',r=>{if(r.status()>=400&&!(expected400&&r.status()===400&&r.url().endsWith('/api/queue')))errors.push(r.status()+' '+r.url())});p.on('requestfailed',r=>{if(!r.failure()?.errorText.includes('ERR_ABORTED'))errors.push(r.failure()?.errorText||'request failed')});await p.goto(base);
await p.getByRole('button',{name:'В очередь · 1 вариантов',exact:true}).click();
assert(await p.getByRole('checkbox',{name:'Автозалив после рендера',exact:true}).isChecked());await p.getByRole('list',{name:'Выбранные каналы'}).getByText('TEST ONLY channel',{exact:true}).waitFor();await p.getByRole('list',{name:'Выбранные исходники'}).getByText('Открытый доступ',{exact:true}).waitFor();
await p.locator('input[type=datetime-local]').fill(new Date(Date.now()+3*3600*1000+120000).toISOString().slice(0,16));
async function shot(name){const bounds=await p.getByRole('dialog').evaluate(e=>({top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,scroll:e.scrollHeight>e.clientHeight}));await p.screenshot({path:path.join(out,name+'.png')});assert(bounds.top>=0&&bounds.bottom<=600&&!bounds.scroll,JSON.stringify(bounds));return bounds}
const scheduleBounds=await shot('schedule');await p.getByRole('button',{name:'Запустить автозалив',exact:true}).evaluate(button=>{button.click();button.click()});
const afterDoubleClick=await wait(s=>s.jobs.some(j=>j.status==='scheduled'&&j.approved&&j.auto_publish&&j.has_output));assert.equal(afterDoubleClick.jobs.length,1);assert.equal((await api('/test/deliveries')).count,0);
await p.getByRole('table').getByText('Запланирован',{exact:true}).waitFor();await p.getByRole('button',{name:'Подробнее',exact:true}).first().click();assert.equal(await p.getByRole('button',{name:'Подтвердить отправку',exact:true}).count(),0);await shot('ready-for-autopublish');await p.getByRole('button',{name:'Закрыть диалог',exact:true}).click();
await api('/test/release','POST');await wait(s=>s.jobs.some(j=>j.status==='platform_processing'));assert.equal((await api('/test/deliveries')).count,1);
await p.getByRole('button',{name:'Монтаж',exact:true}).click();await p.getByRole('tab',{name:'Публикация',exact:true}).click();await p.getByLabel('Название для YouTube',{exact:true}).fill('Synthetic manual review');await p.getByRole('button',{name:'В очередь · 1 вариантов',exact:true}).click();await p.getByRole('checkbox',{name:'Автозалив после рендера',exact:true}).uncheck();await p.locator('input[type=datetime-local]').fill('');await p.getByRole('button',{name:'Создать MP4',exact:true}).click();await wait(s=>s.jobs.some(j=>j.status==='ready'&&!j.approved&&!j.auto_publish));assert.equal((await api('/test/deliveries')).count,1);
await api('/test/buba','POST');await p.reload();await p.getByRole('button',{name:'Монтаж',exact:true}).click();await p.getByRole('button',{name:'В очередь · 1 вариантов',exact:true}).click();assert.equal(await p.getByRole('checkbox',{name:/Подтверждаю для выбранных роликов условия БУБА/}).isChecked(),false);const bubaBounds=await shot('buba-confirmation');
await p.getByRole('button',{name:'Закрыть диалог',exact:true}).click();await api('/test/bulk','POST');await p.reload();await p.getByRole('button',{name:'Очередь',exact:true}).click();await p.getByRole('button',{name:'Пакет из всех исходников',exact:true}).click();const bulkBounds=await shot('bulk-long-names');
await p.getByRole('button',{name:'Закрыть диалог',exact:true}).click();await api('/test/many','POST');await p.reload();await p.getByRole('button',{name:'Очередь',exact:true}).click();await p.getByRole('button',{name:'Пакет из всех исходников',exact:true}).click();const pagedBounds=await shot('thirty-sources');await p.getByRole('dialog').getByRole('button',{name:'Следующая страница',exact:true}).click();await shot('thirty-sources-page-2');
expected400=true;await p.getByRole('button',{name:'Запустить автозалив',exact:true}).click();await p.getByRole('dialog',{name:'Автозалив · проверь параметры',exact:true}).waitFor();const errorBounds=await shot('bulk-validation-error');assert((await p.getByRole('alert').innerText()).includes('БУБА'));assert.equal((await api('/test/deliveries')).count,1);
assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'ui-results.json'),JSON.stringify({synthetic:true,realFFmpeg:true,remoteUploadsMocked:true,automaticRenderAndDispatch:true,noExtraApproval:true,renderOnlyStillWorks:true,duplicateRemoteWrites:0,doubleClickSingleJob:true,scheduleBounds,bubaBounds,bulkBounds,pagedBounds,errorBounds,expectedValidationError:true,errors},null,2));console.log(JSON.stringify({scheduleBounds,bubaBounds,bulkBounds,pagedBounds,errorBounds,errors}));
}finally{await browser?.close();server.kill()}})().catch(e=>{console.error(e);process.exitCode=1});
