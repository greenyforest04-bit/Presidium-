import type {MatrixSession} from './matrix-session';

// Separate from content backups. This store never contains plaintext, tokens or keys.
export type QueuedCipher={id:string;scope:string;roomId:string;txnId:string;createdAt:number;recipients:string;payload:string};
export type QueuedSummary=Pick<QueuedCipher,'id'|'roomId'|'txnId'|'createdAt'>;
export type QueueScope={scope:string;ownerHash:string;baseUrl:string;userId:string;deviceId:string;createdAt:number};
export type PreviousQueue=QueueScope&{count:number;oldest:number};
const databaseName='presidium-matrix-outbox-v1',limit=100;
export async function digest(value:string){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),v=>v.toString(16).padStart(2,'0')).join('')}
export function cipherPayload(raw:unknown):string{
 if(typeof raw!=='string'||raw.length>64000)throw Error('Некорректный размер зашифрованного сообщения.');
 const value=JSON.parse(raw);const allowed=['algorithm','ciphertext','session_id','sender_key','device_id','m.relates_to'];
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!allowed.includes(k))||value.algorithm!=='m.megolm.v1.aes-sha2'||typeof value.ciphertext!=='string'||!value.ciphertext||typeof value.session_id!=='string'||!value.session_id||['sender_key','device_id'].some(k=>value[k]!==undefined&&typeof value[k]!=='string'))throw Error('Очередь принимает только сообщения, зашифрованные Matrix.');
 const relation=value['m.relates_to'];
 if(relation!==undefined){const reply=relation?.['m.in_reply_to'],validId=(id:unknown)=>typeof id==='string'&&/^\$\S{1,511}$/.test(id);const replyOnly=!!reply&&typeof reply==='object'&&!Array.isArray(reply)&&Object.keys(relation).length===1&&Object.keys(reply).length===1&&validId(reply.event_id),replaceOnly=relation?.rel_type==='m.replace'&&Object.keys(relation).length===2&&validId(relation.event_id);if(!relation||typeof relation!=='object'||Array.isArray(relation)||!replyOnly&&!replaceOnly)throw Error('Очередь принимает только ссылки ответа или изменения без открытого текста.');}
 return JSON.stringify(value);
}
function valid(row:QueuedCipher,scope:string){if(!row||row.scope!==scope||row.id!==scope+':'+row.txnId||typeof row.roomId!=='string'||!row.roomId.startsWith('!')||!/^presidium-[a-f0-9-]{36}$/.test(row.txnId)||!Number.isFinite(row.createdAt)||!/^[a-f0-9]{64}$/.test(row.recipients))throw Error('Очередь сообщений повреждена.');cipherPayload(row.payload);return row}
function openDatabase(){return new Promise<IDBDatabase>((resolve,reject)=>{let blocked=false;const request=indexedDB.open(databaseName,2);request.onupgradeneeded=()=>{const db=request.result;if(!db.objectStoreNames.contains('events')){const store=db.createObjectStore('events',{keyPath:'id'});store.createIndex('scope','scope')}if(!db.objectStoreNames.contains('scopes')){const store=db.createObjectStore('scopes',{keyPath:'scope'});store.createIndex('ownerHash','ownerHash')}};request.onsuccess=()=>{const db=request.result;if(blocked){db.close();return}db.onversionchange=()=>db.close();resolve(db)};request.onerror=()=>reject(Error('Не удалось открыть очередь сообщений.'));request.onblocked=()=>{blocked=true;reject(Error('Закройте другую вкладку, чтобы обновить очередь сообщений.'))}})}
async function registryTransaction<T>(mode:IDBTransactionMode,action:(tx:IDBTransaction,result:(value:T)=>void,guard:(fn:()=>void)=>()=>void)=>void){
 const db=await openDatabase();return new Promise<T>((resolve,reject)=>{
  const tx=db.transaction(['scopes','events'],mode);let result:T,failure:unknown;
  const guard=(fn:()=>void)=>()=>{try{fn()}catch(error){failure=error;tx.abort()}};
  tx.oncomplete=()=>{db.close();resolve(result)};
  tx.onabort=()=>{db.close();reject(failure||tx.error||Error('Изменение очереди отменено.'))};tx.onerror=()=>{};
  guard(()=>action(tx,value=>{result=value},guard))();
 })
}
function isAllowedScopeUrl(url:URL):boolean{
 if(!url.hostname||url.username||url.password||url.search||url.hash)return false;
 if(url.protocol==='https:')return true;
 if(url.protocol==='http:'&&(url.hostname==='localhost'||url.hostname==='127.0.0.1'||url.hostname==='[::1]'||url.hostname==='::1'))return true;
 return false;
}
function validScope(row:QueueScope,ownerHash:string){
 if(!row||row.ownerHash!==ownerHash||!/^[a-f0-9]{64}$/.test(row.scope)||typeof row.baseUrl!=='string'||typeof row.userId!=='string'||!row.userId.startsWith('@')||typeof row.deviceId!=='string'||!row.deviceId||!Number.isFinite(row.createdAt))throw Error('Описание очереди повреждено.');
 let url:URL;try{url=new URL(row.baseUrl)}catch{throw Error('Адрес сервера очереди повреждён.')}
 if(!isAllowedScopeUrl(url))throw Error('Адрес сервера очереди повреждён.');return row
}
export class MatrixOutbox{
 private constructor(readonly scope:string){}
 static async forSession(owner:string,session:MatrixSession){
  if(!owner)throw Error('Нет владельца очереди сообщений.');
  const scope=await digest(JSON.stringify([owner,session.baseUrl,session.userId,session.deviceId])),ownerHash=await digest(owner);
  const descriptor=validScope({scope,ownerHash,baseUrl:session.baseUrl,userId:session.userId,deviceId:session.deviceId,createdAt:Date.now()},ownerHash);
  await registryTransaction<void>('readwrite',(tx,_result,guard)=>{
   const store=tx.objectStore('scopes'),request=store.get(scope);
   request.onsuccess=guard(()=>{if(request.result){validScope(request.result,ownerHash);return}store.put(descriptor)})
  });return new MatrixOutbox(scope)
 }
 static async previous(owner:string){
  if(!owner)throw Error('Нет владельца очереди.');const ownerHash=await digest(owner);
  return registryTransaction<PreviousQueue[]>('readonly',(tx,result,guard)=>{
   const request=tx.objectStore('scopes').index('ownerHash').getAll(ownerHash);
   request.onsuccess=guard(()=>{
    const rows:PreviousQueue[]=[],scopes=request.result as QueueScope[];let pending=scopes.length;
    if(!pending){result([]);return}
    for(const item of scopes){
     const scope=validScope(item,ownerHash),events=tx.objectStore('events').index('scope').getAll(scope.scope);
     events.onsuccess=guard(()=>{const stored=(events.result as QueuedCipher[]).map(row=>valid(row,scope.scope));if(stored.length)rows.push({...scope,count:stored.length,oldest:Math.min(...stored.map(row=>row.createdAt))});if(!--pending)result(rows.sort((a,b)=>a.oldest-b.oldest))})
    }
   })
  })
 }
 static async removePrevious(owner:string,scope:string,expectedCount:number){
  if(!owner||!Number.isInteger(expectedCount)||expectedCount<1)throw Error('Сначала обновите список очередей.');const ownerHash=await digest(owner);
  await registryTransaction<void>('readwrite',(tx,_result,guard)=>{
   const request=tx.objectStore('scopes').get(scope);
   request.onsuccess=guard(()=>{
    if(!request.result||request.result.ownerHash!==ownerHash)throw Error('Очередь принадлежит другому профилю или уже удалена.');
    validScope(request.result,ownerHash);const events=tx.objectStore('events').index('scope').getAllKeys(scope);
    events.onsuccess=guard(()=>{
     if(events.result.length!==expectedCount)throw Error('Количество сообщений изменилось. Обновите список и подтвердите удаление заново.');
     for(const key of events.result)tx.objectStore('events').delete(key);tx.objectStore('scopes').delete(scope)
    })
   })
  })
 }
 private async transaction<T>(mode:IDBTransactionMode,action:(store:IDBObjectStore,result:(value:T)=>void)=>void):Promise<T>{const db=await openDatabase();return new Promise<T>((resolve,reject)=>{const tx=db.transaction('events',mode);let result:T;tx.oncomplete=()=>{db.close();resolve(result)};tx.onabort=()=>{db.close();reject(tx.error||Error('Не удалось сохранить очередь. Сообщение не отправлено.'))};tx.onerror=()=>{};try{action(tx.objectStore('events'),value=>{result=value})}catch(error){tx.abort();db.close();reject(error)}})}
 async list(){const rows=await this.transaction<QueuedCipher[]>('readonly',(store,result)=>{const request=store.index('scope').getAll(this.scope);request.onsuccess=()=>result(request.result)});return rows.map(row=>valid(row,this.scope)).sort((a,b)=>a.createdAt-b.createdAt)}
 async get(txnId:string){const row=await this.transaction<QueuedCipher|undefined>('readonly',(store,result)=>{const request=store.get(this.scope+':'+txnId);request.onsuccess=()=>result(request.result)});return row?valid(row,this.scope):undefined}
 async put(input:Omit<QueuedCipher,'id'|'scope'>){const row=valid({...input,payload:cipherPayload(input.payload),scope:this.scope,id:this.scope+':'+input.txnId},this.scope);let conflict:Error|undefined;
  try{await this.transaction<void>('readwrite',(store)=>{const request=store.get(row.id);request.onsuccess=()=>{const previous=request.result as QueuedCipher|undefined;if(previous){if(previous.payload!==row.payload||previous.roomId!==row.roomId||previous.recipients!==row.recipients){conflict=Error('Повторная отправка изменила сообщение. Используйте сохранённую очередь.');request.transaction!.abort()}return}const count=store.index('scope').count(this.scope);count.onsuccess=()=>{if(count.result>=limit){conflict=Error('В очереди 100 сообщений. Отправьте или удалите часть из них.');count.transaction!.abort();return}store.add(row)}}})}catch(error){throw conflict||error}return row;
 }
 async remove(txnId:string){await this.transaction<void>('readwrite',store=>{store.delete(this.scope+':'+txnId)})}
}
