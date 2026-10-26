const encoder = new TextEncoder();
const hex = (buffer) => [...new Uint8Array(buffer)].map(v=>v.toString(16).padStart(2,'0')).join('');
export async function sha(value) { return hex(await crypto.subtle.digest('SHA-256',encoder.encode(value))); }
export async function sign(code, message) {
  const key=await crypto.subtle.importKey('raw',encoder.encode(code),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  return hex(await crypto.subtle.sign('HMAC',key,encoder.encode(message)));
}
export async function checkServer(code, clientNonce, reply) {
  if(typeof reply.server_nonce!=='string'||!reply.server_nonce.match(/^[A-Za-z0-9_-]{16,64}$/))return false;
  const expected=await sign(code,`server|${clientNonce}|${reply.server_nonce}`);
  return expected===reply.server_proof;
}
export function cleanCookies(cookies) {
  return cookies.filter(c=>['tiktok.com','www.tiktok.com'].includes(c.domain.replace(/^\./,'')) && !c.partitionKey).map(c=>({
    name:c.name,value:c.value,domain:c.domain,path:c.path,
    expires:c.session ? -1 : (c.expirationDate ?? -1),httpOnly:c.httpOnly,secure:c.secure,
    sameSite:({strict:'Strict',lax:'Lax',no_restriction:'None',unspecified:'Lax'})[c.sameSite] ?? 'Lax',
  }));
}
// Runs only in the current TikTok tab, following an explicit click in the popup.
export function ownProfile() {
  if(location.protocol!=='https:'||!['www.tiktok.com','tiktok.com'].includes(location.hostname))return null;
  const links=[...document.querySelectorAll('[data-e2e="nav-profile"],[data-e2e="nav-profile-link"]')].map(n=>(n.closest('a')||n.querySelector('a'))?.href).filter(Boolean);
  if(document.querySelector('[data-e2e="edit-profile-entrance"],[data-e2e="edit-profile-button"]'))links.push(location.href);
  const handles=new Set();
  for(const link of links){try{const u=new URL(link);const m=decodeURIComponent(u.pathname).match(/^\/@([A-Za-z0-9_.]{2,24})\/?$/);if(u.protocol==='https:'&&['www.tiktok.com','tiktok.com'].includes(u.hostname)&&m)handles.add(m[1]);}catch{}}
  return handles.size===1?[...handles][0]:null;
}
