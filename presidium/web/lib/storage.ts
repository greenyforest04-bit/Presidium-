import type {State,Story} from './domain';

// Keep the installed database and store names stable. Device keys never enter
// a snapshot or an import transaction.
type Store='state'|'blobs'|'keys';
export type StoredMedia={id:string;blob:Blob};
// Avoid retaining the browser-owned backing file of a recorded File/Blob.
export type MediaBytes={format:'presidium-bytes-v1';bytes:ArrayBuffer;mime:string};
export function mediaBlob(value:unknown):Blob|undefined{
 if(value instanceof Blob)return value;
 const item=value as Partial<MediaBytes>|undefined;
 if(item?.format==='presidium-bytes-v1'&&item.bytes instanceof ArrayBuffer&&typeof item.mime==='string')return new Blob([item.bytes],{type:item.mime});
}
export type StoryDraft={story:Story;owned:string[]};
export type LocalSnapshot={state:State;blobs:StoredMedia[];storyDraft?:StoryDraft};
let owner='anonymous';
export const storageOwner=()=>owner;
export function setStorageOwner(value:string){owner=value}
export const scopeKey=(key:string)=>key+':matrix-owner:'+owner;
export const blobKey=(key:string)=>scopeKey('blob:'+key);
async function ownBlob(db:IDBDatabase,key:string,expected=owner):Promise<Blob|undefined>{
 const scoped='blob:'+key+':matrix-owner:'+expected;
 return new Promise((resolve,reject)=>{
  const tx=db.transaction(['state','blobs']),store=tx.objectStore('blobs');
  const current=store.get(scoped),legacy=store.get(key),names=tx.objectStore('state').getAllKeys(),values=tx.objectStore('state').getAll();
  tx.oncomplete=()=>{
   if(owner!==expected){reject(Error('Аккаунт изменился. Чтение отменено.'));return}
   if(current.result!==undefined){resolve(mediaBlob(current.result));return}
   // Preserve installed files: a legacy blob can only be reached through an
   // already-owned record (or an explicitly account-scoped old market key).
   const suffix=':matrix-owner:'+expected,needle=JSON.stringify(key),photoId=key.split(':').at(-1)!;
   const allowed=expected!=='anonymous'&&(key.includes(expected)||names.result.some((name,i)=>String(name).endsWith(suffix)&&
    (JSON.stringify(values.result[i]).includes(needle)||(key.startsWith('commissionka-')&&JSON.stringify(values.result[i]).includes(JSON.stringify(photoId))))));
   resolve(allowed?mediaBlob(legacy.result):undefined);
  };
  tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
 });
}
const clean=(state:State):State=>({...state,spaces:state.spaces.filter(s=>!s.id.startsWith('!')),messages:state.messages.filter(m=>!m.spaceId.startsWith('!'))});
let connection:Promise<IDBDatabase>|undefined;
export function database(){
 if(!connection){const opening=new Promise<IDBDatabase>((resolve,reject)=>{
  const request=indexedDB.open('presidium-local-v1',1);
  request.onupgradeneeded=()=>{for(const name of ['state','blobs','keys'])if(!request.result.objectStoreNames.contains(name))request.result.createObjectStore(name)};
  request.onerror=()=>{if(connection===opening)connection=undefined;reject(request.error)};
  let blocked=false;
  request.onblocked=()=>{blocked=true;if(connection===opening)connection=undefined;reject(Error('Закройте другие вкладки Presidium и попробуйте снова.'))};
  request.onsuccess=()=>{const db=request.result;if(blocked){db.close();return}const forget=()=>{if(connection===opening)connection=undefined};db.onclose=forget;db.onversionchange=()=>{forget();db.close()};resolve(db)};
 });connection=opening;}
 return connection;
}
export async function read<T>(store:Store,key:string):Promise<T|undefined>{
 if(store==='blobs'){const expected=owner,db=await database();return await ownBlob(db,key,expected) as T|undefined}
 if(store==='state')key=scopeKey(key);
 const db=await database();return new Promise((resolve,reject)=>{const tx=db.transaction(store),request=tx.objectStore(store).get(key);let value:T|undefined;request.onsuccess=()=>{value=request.result as T|undefined};tx.oncomplete=()=>resolve(value);tx.onabort=()=>reject(tx.error||Error('Чтение прервано'));tx.onerror=()=>reject(tx.error)});
}
export async function write(store:Store,key:string,value:unknown){
 if(store==='blobs')key=blobKey(key);
 if(store==='state'){if(key==='current')value=clean(value as State);key=scopeKey(key);}
 const db=await database();return new Promise<void>((resolve,reject)=>{const tx=db.transaction(store,'readwrite');tx.objectStore(store).put(value,key);tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error||Error('Сохранение прервано'));tx.onerror=()=>reject(tx.error)});
}
export async function removeBlob(key:string){key=blobKey(key);const db=await database();return new Promise<void>((resolve,reject)=>{const tx=db.transaction('blobs','readwrite');tx.objectStore('blobs').delete(key);tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error);tx.onerror=()=>reject(tx.error)})}
/** Compare references and delete within one transaction; never touch device keys. */
export async function pruneMedia(ids:string[],references:(state:State,draft?:StoryDraft)=>string[]):Promise<string[]>{
 if(!ids.length)return [];
 const prefixOwner=owner,currentKey=scopeKey('current'),draftKey=scopeKey('story-draft-v2');const db=await database();return new Promise((resolve,reject)=>{
  const tx=db.transaction(['state','blobs'],'readwrite'),current=tx.objectStore('state').get(currentKey),draft=tx.objectStore('state').get(draftKey);let deleted:string[]=[],problem:unknown,pending=2;
  const collect=()=>{if(--pending||!current.result)return;try{const savedDraft=draft.result as StoryDraft|undefined;const keep=new Set([...references(current.result,savedDraft),...(savedDraft?.owned||[])]);deleted=[...new Set(ids)].filter(id=>!keep.has(id));for(const id of deleted)tx.objectStore('blobs').delete('blob:'+id+':matrix-owner:'+prefixOwner)}catch(error){problem=error;tx.abort()}};
  current.onsuccess=collect;draft.onsuccess=collect;
  tx.oncomplete=()=>resolve(deleted);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(problem||tx.error||Error('Очистка вложений прервана'));
 });
}
export async function saveStoryState(state:State,clearDraft=false){
 const currentKey=scopeKey('current'),draftKey=scopeKey('story-draft-v2');const db=await database();return new Promise<void>((resolve,reject)=>{const tx=db.transaction('state','readwrite'),store=tx.objectStore('state');store.put(clean(state),currentKey);if(clearDraft)store.delete(draftKey);tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error);tx.onerror=()=>reject(tx.error)});
}
export async function saveMediaState(state:State,id:string,media:MediaBytes){
 const currentKey=scopeKey('current'),mediaKey=blobKey(id);
 // Encoding must finish before the transaction: awaiting Blob.arrayBuffer()
 // inside it lets IDB auto-commit before the message can be written.
 for(let attempt=0;attempt<2;attempt++){
  const db=await database();
  try{
   await new Promise<void>((resolve,reject)=>{
    let tx:IDBTransaction;
    try{tx=db.transaction(['state','blobs'],'readwrite')}catch(error){reject(error);return}
    let problem:unknown;
    tx.oncomplete=()=>resolve();
    tx.onerror=event=>{problem??=(event.target as IDBRequest)?.error||tx.error};
    tx.onabort=()=>reject(problem||tx.error||new DOMException('Сохранение прервано','AbortError'));
    try{tx.objectStore('blobs').put(media,mediaKey);tx.objectStore('state').put(clean(state),currentKey)}
    catch(error){problem=error;tx.abort()}
   });
   return;
  }catch(problem){
   const name=(problem as {name?:string}|null)?.name;
   if(attempt||!['InvalidStateError','UnknownError'].includes(name||''))throw problem;
   // Retry only a failed transaction, with the same IDs and bytes. Never
   // recreate the installed database or remove local content/device keys.
   const previous=connection;if(previous&&await previous===db)connection=undefined;db.close();
  }
 }
}
export async function readSnapshot(fallback:State,idsFor:(state:State,draft?:StoryDraft)=>string[]):Promise<LocalSnapshot>{
 const expected=owner,currentKey=scopeKey('current'),draftKey=scopeKey('story-draft-v2'),db=await database();
 return new Promise((resolve,reject)=>{
  const tx=db.transaction(['state','blobs']);let state=fallback,storyDraft:StoryDraft|undefined,pending=2;const blobs:StoredMedia[]=[];
  const media=()=>{if(--pending)return;try{for(const id of idsFor(state,storyDraft)){
   const current=tx.objectStore('blobs').get('blob:'+id+':matrix-owner:'+expected),legacy=tx.objectStore('blobs').get(id);let reads=2;
   const done=()=>{if(--reads)return;const blob=mediaBlob(current.result??legacy.result);if(blob)blobs.push({id,blob})};current.onsuccess=done;legacy.onsuccess=done;
  }}catch(error){tx.abort();reject(error)}};
  const current=tx.objectStore('state').get(currentKey),draft=tx.objectStore('state').get(draftKey);
  current.onsuccess=()=>{if(current.result)state=current.result;media()};draft.onsuccess=()=>{storyDraft=draft.result||undefined;media()};
  tx.oncomplete=()=>owner===expected?resolve({state,storyDraft,blobs}):reject(Error('Аккаунт изменился. Экспорт отменён.'));
  tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
 });
}
export async function commitImport(fallback:State,merge:(current:State,hasDraft:boolean)=>LocalSnapshot):Promise<State>{
 const expected=owner,currentKey=scopeKey('current'),draftKey=scopeKey('story-draft-v2');const db=await database();return new Promise((resolve,reject)=>{
  const tx=db.transaction(['state','blobs'],'readwrite');let next:State;
  const current=tx.objectStore('state').get(currentKey),draft=tx.objectStore('state').get(draftKey);let pending=2;
  function apply(){if(--pending)return;try{
   const prepared=merge(current.result||fallback,!!draft.result);next=prepared.state;
   // Every remapped file and the state that refers to it commit together.
   for(const media of prepared.blobs)tx.objectStore('blobs').add(media.blob,'blob:'+media.id+':matrix-owner:'+expected);
   tx.objectStore('state').put(clean(next),currentKey);
   if(prepared.storyDraft&&!draft.result)tx.objectStore('state').put(prepared.storyDraft,draftKey);
  }catch(error){tx.abort();reject(error)}}
  current.onsuccess=apply;draft.onsuccess=apply;
  tx.oncomplete=()=>resolve(next!);tx.onabort=()=>reject(tx.error||Error('Восстановление прервано. Текущие данные сохранены.'));tx.onerror=()=>reject(tx.error);
 });
}
