import { sha, sign, checkServer, cleanCookies, ownProfile } from './bridge.js';
const button=document.querySelector('#connect'), status=document.querySelector('#status');
const message=(text,error=false)=>{status.textContent=text;status.dataset.error=String(error);};
button.addEventListener('click',async()=>{
 button.disabled=true;
 try {
  const code=document.querySelector('#code').value.trim();
  if(!/^[A-Za-z0-9_-]{32}$/.test(code))throw Error('Скопируйте действующий код подключения из SurvivalFlow.');
  const port=Number(document.querySelector('#port').value);
  if(!Number.isInteger(port)||port<1024||port>65535)throw Error('Проверьте порт локальной программы.');
  const base=`http://127.0.0.1:${port}`;
  const post=async(path,body)=>{
   let response;
   try{response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});}catch{throw Error('Нет связи с SurvivalFlow. Запустите программу и начните подключение.');}
   let data;try{data=await response.json();}catch{throw Error('На этом порту не отвечает SurvivalFlow.');}
   if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Проверьте код подключения.');
   return data;
  };
  const codeId=await sha(code), clientNonce=crypto.randomUUID().replaceAll('-','');
  const reply=await post('/api/browser-pairing/check',{code_id:codeId,client_nonce:clientNonce});
  if(!await checkServer(code,clientNonce,reply))throw Error('Программа не подтвердила код. Сессия TikTok не передавалась.');
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  if(!tab?.id||!tab.url)throw Error('Откройте свой профиль TikTok в текущей вкладке.');
  const url=new URL(tab.url);
  if(url.protocol!=='https:'||!['tiktok.com','www.tiktok.com'].includes(url.hostname))throw Error('Переключитесь на вкладку TikTok и откройте расширение там.');
  const result=await chrome.scripting.executeScript({target:{tabId:tab.id},func:ownProfile});
  const handle=result[0]?.result;
  if(!handle)throw Error('Не удалось определить собственный аккаунт. Войдите в TikTok и откройте свой «Профиль».');
  const stores=await chrome.cookies.getAllCookieStores();
  const store=stores.find(s=>s.tabIds.includes(tab.id));
  if(!store)throw Error('Не найдено хранилище текущей вкладки Chrome.');
  const cookies=cleanCookies(await chrome.cookies.getAll({domain:'tiktok.com',storeId:store.id}));
  if(!cookies.some(c=>c.name==='sessionid'&&c.value&&(c.expires===-1||c.expires>Date.now()/1000)))throw Error('Нет действующей сессии TikTok. Завершите вход на сайте.');
  const payload=JSON.stringify({handle,cookies});
  const proof=await sign(code,`client|${clientNonce}|${reply.server_nonce}|${await sha(payload)}`);
  const saved=await post('/api/browser-pairing/submit',{code_id:codeId,client_nonce:clientNonce,server_nonce:reply.server_nonce,payload,proof});
  document.querySelector('#code').value='';
  message(`Найден @${saved.handle}. Вернитесь в SurvivalFlow и подтвердите сохранение этого аккаунта.`);
 }catch(error){message(error.message || 'Не удалось подключить аккаунт.',true);}
 finally{button.disabled=false;}
});
