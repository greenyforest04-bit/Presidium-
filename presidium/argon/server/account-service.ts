import {AccountError,type AccountDevice,type AccountProfile,type ProfileInput} from '../lib/account';
type SessionRow={id:string;user_id:string;label:string;created_at:number;last_seen_at:number;expires_at:number};
type ProfileRow={user_id:string;name:string;handle:string|null;bio:string;revision:number;updated_at:number};
export const SESSION_SECONDS=30*24*60*60;
export const DEVICE_COOKIE='__Host-presidium-device';
export const LOCAL_HTTP_DEVICE_COOKIE='presidium-device';

export function isLocalDevLoopbackHttp(request?:Request):boolean{
 if(!request)return false;
 if(typeof process!=='undefined'&&(process.env?.NODE_ENV==='production'||process.env?.PRESIDIUM_LOCAL_DEV_AUTH!=='1'))return false;
 try{
  const u=new URL(request.url);
  if(u.protocol!=='http:')return false;
  return u.hostname==='localhost'||u.hostname==='127.0.0.1'||u.hostname==='[::1]'||u.hostname==='::1';
 }catch{return false}
}

export function deviceToken(request:Request){
 const parts=request.headers.get('cookie')?.split(';').map(v=>v.trim())||[];
 const primary=parts.find(v=>v.startsWith(DEVICE_COOKIE+'='))?.slice(DEVICE_COOKIE.length+1);
 if(primary&&/^[a-f0-9]{64}$/.test(primary))return primary;
 if(isLocalDevLoopbackHttp(request)){
  const fallback=parts.find(v=>v.startsWith(LOCAL_HTTP_DEVICE_COOKIE+'='))?.slice(LOCAL_HTTP_DEVICE_COOKIE.length+1);
  if(fallback&&/^[a-f0-9]{64}$/.test(fallback))return fallback;
 }
 return undefined;
}
export function deviceCookie(token:string){return `${DEVICE_COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_SECONDS}`}
export function expiredDeviceCookie(){return `${DEVICE_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`}
export function deviceCookieForRequest(token:string,request?:Request){
 if(isLocalDevLoopbackHttp(request))return `${LOCAL_HTTP_DEVICE_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_SECONDS}`;
 return deviceCookie(token);
}
export function expiredDeviceCookieForRequest(request?:Request){
 if(isLocalDevLoopbackHttp(request))return `${LOCAL_HTTP_DEVICE_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`;
 return expiredDeviceCookie();
}
async function digest(token:string){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(token));return Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,'0')).join('')}
function profileOf(row:ProfileRow):AccountProfile{return {userId:row.user_id,name:row.name,handle:row.handle||'',bio:row.bio,revision:row.revision,updatedAt:row.updated_at}}
function conflict(error:unknown):never{if(String(error).includes('UNIQUE constraint failed: profiles.handle'))throw new AccountError('Это имя пользователя уже занято.',409);throw error}
export class AccountService{
 constructor(private db:D1Database,private userId:string){}
 async session(token?:string):Promise<SessionRow|null>{
  if(!token)return null;const now=Date.now(),hash=await digest(token);
  return this.db.prepare('SELECT id,user_id,label,created_at,last_seen_at,expires_at FROM sessions WHERE token_hash=? AND user_id=? AND revoked_at IS NULL AND expires_at>?').bind(hash,this.userId,now).first<SessionRow>();
 }
 async requireSession(token?:string):Promise<SessionRow>{const current=await this.session(token);if(!current)throw new AccountError('Сеанс этого устройства не активен. Подключите устройство заново.',401,'device_session_required');return current}
 async touch(current:SessionRow|null){if(!current||Date.now()-current.last_seen_at<300000)return;await this.db.prepare('UPDATE sessions SET last_seen_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL').bind(Date.now(),current.id,this.userId).run()}
 async profile():Promise<AccountProfile|null>{const row=await this.db.prepare('SELECT user_id,name,handle,bio,revision,updated_at FROM profiles WHERE user_id=?').bind(this.userId).first<ProfileRow>();return row?profileOf(row):null}
 async devices(current:SessionRow|null):Promise<AccountDevice[]>{
  if(!current)return [];
  const rows=await this.db.prepare('SELECT id,user_id,label,created_at,last_seen_at,expires_at FROM sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY last_seen_at DESC LIMIT 20').bind(this.userId,Date.now()).all<SessionRow>();
  return rows.results.map(r=>({id:r.id,label:r.label,createdAt:r.created_at,lastSeenAt:r.last_seen_at,expiresAt:r.expires_at,current:r.id===current.id}));
 }
 async connect(input:ProfileInput,deviceName:string,existingToken?:string):Promise<{profile:AccountProfile;token?:string}>{
  const now=Date.now(),handle=input.handle||null,current=await this.session(existingToken);
  if(current){
   await this.db.prepare('UPDATE sessions SET label=?,last_seen_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL').bind(deviceName,now,current.id,this.userId).run();
   const existing=await this.profile();if(existing)return {profile:existing};
   try{await this.db.batch([
    this.db.prepare('INSERT INTO users(id,created_at) VALUES(?,?) ON CONFLICT(id) DO NOTHING').bind(this.userId,now),
    this.db.prepare('INSERT INTO profiles(user_id,name,handle,bio,revision,updated_at) VALUES(?,?,?,?,1,?) ON CONFLICT(user_id) DO NOTHING').bind(this.userId,input.name,handle,input.bio,now)
   ])}catch(error){conflict(error)}
   const created=await this.profile();if(!created)throw new AccountError('Не удалось создать профиль.',500);
   return {profile:created};
  }
  const active=await this.db.prepare('SELECT COUNT(*) AS count FROM sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>?').bind(this.userId,now).first<{count:number}>();
  if((active?.count||0)>=20)throw new AccountError('Достигнут лимит активных устройств (20). Завершите старый сеанс.',409);
  const bytes=crypto.getRandomValues(new Uint8Array(32)),token=Array.from(bytes,v=>v.toString(16).padStart(2,'0')).join(''),hash=await digest(token);
  try{
   const results=await this.db.batch([
    this.db.prepare('INSERT INTO users(id,created_at) VALUES(?,?) ON CONFLICT(id) DO NOTHING').bind(this.userId,now),
    this.db.prepare('INSERT INTO profiles(user_id,name,handle,bio,revision,updated_at) VALUES(?,?,?,?,1,?) ON CONFLICT(user_id) DO NOTHING').bind(this.userId,input.name,handle,input.bio,now),
    this.db.prepare('INSERT INTO sessions(id,user_id,token_hash,label,created_at,last_seen_at,expires_at) SELECT ?,?,?,?,?,?,? WHERE (SELECT COUNT(*) FROM sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>?)<20').bind(crypto.randomUUID(),this.userId,hash,deviceName,now,now,now+SESSION_SECONDS*1000,this.userId,now)
   ]);
   if(!results[2].meta.changes)throw new AccountError('Достигнут лимит активных устройств (20). Завершите старый сеанс.',409);
  }catch(error){conflict(error)}
  const saved=await this.profile();if(!saved)throw new AccountError('Профиль не найден после сохранения.',500);
  return {profile:saved,token};
 }
 async update(input:ProfileInput,revision:number,token?:string):Promise<AccountProfile>{
  const current=await this.requireSession(token),now=Date.now(),handle=input.handle||null;
  try{
   const result=await this.db.prepare('UPDATE profiles SET name=?,handle=?,bio=?,revision=revision+1,updated_at=? WHERE user_id=? AND revision=? AND EXISTS(SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL AND expires_at>?)').bind(input.name,handle,input.bio,now,this.userId,revision,current.id,this.userId,now).run();
   if(!result.meta.changes){await this.requireSession(token);throw new AccountError('Профиль был изменён на другом устройстве. Загрузите свежую версию перед сохранением.',409,'revision_conflict')}
  }catch(error){conflict(error)}
  const updated=await this.profile();if(!updated)throw new AccountError('Профиль не найден.',404);return updated;
 }
 async revoke(sessionId:string,token?:string):Promise<boolean>{
  const current=await this.requireSession(token),now=Date.now();
  const result=await this.db.batch([
   this.db.prepare('UPDATE sessions SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL AND EXISTS(SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL AND expires_at>?)').bind(now,sessionId,this.userId,current.id,this.userId,now),
   this.db.prepare('DELETE FROM device_challenges WHERE session_id=? AND EXISTS(SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NOT NULL)').bind(sessionId,sessionId,this.userId),
   this.db.prepare('DELETE FROM device_identities WHERE session_id=? AND user_id=? AND EXISTS(SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NOT NULL)').bind(sessionId,this.userId,sessionId,this.userId)
  ]);
  if(!result[0].meta.changes){await this.requireSession(token);throw new AccountError('Сеанс уже завершён или не найден.',404)}
  return sessionId===current.id;
 }
}
