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
 const db=await database();return new Promise((resolve,reject)=>{const tx=db.transaction(store),request=tx.objectStore(store).get(key);let value:T|undefined;request.onsuccess=()=>{value=(store==='blobs'?mediaBlob(request.result):request.result) as T|undefined};tx.oncomplete=()=>resolve(value);tx.onabort=()=>reject(tx.error||Error('Чтение прервано'));tx.onerror=()=>reject(tx.error)});
}
export async function write(store:Store,key:string,value:unknown){
 const db=await database();return new Promise<void>((resolve,reject)=>{const tx=db.transaction(store,'readwrite');tx.objectStore(store).put(value,key);tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error||Error('Сохранение прервано'));tx.onerror=()=>reject(tx.error)});
}
export async function removeBlob(key:string){const db=await database();return new Promise<void>((resolve,reject)=>{const tx=db.transaction('blobs','readwrite');tx.objectStore('blobs').delete(key);tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error);tx.onerror=()=>reject(tx.error)})}
/** Compare references and delete within one transaction; never touch device keys. */
export async function pruneMedia(ids:string[],references:(state:State,draft?:StoryDraft)=>string[]):Promise<string[]>{
 if(!ids.length)return [];
 const db=await database();return new Promise((resolve,reject)=>{
  const tx=db.transaction(['state','blobs'],'readwrite'),current=tx.objectStore('state').get('current'),draft=tx.objectStore('state').get('story-draft-v2');let deleted:string[]=[],problem:unknown,pending=2;
  const collect=()=>{if(--pending||!current.result)return;try{const savedDraft=draft.result as StoryDraft|undefined;const keep=new Set([...references(current.result,savedDraft),...(savedDraft?.owned||[])]);deleted=[...new Set(ids)].filter(id=>!keep.has(id));for(const id of deleted)tx.objectStore('blobs').delete(id)}catch(error){problem=error;tx.abort()}};
  current.onsuccess=collect;draft.onsuccess=collect;
  tx.oncomplete=()=>resolve(deleted);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(problem||tx.error||Error('Очистка вложений прервана'));
 });
}
export async function saveStoryState(state:State,clearDraft=false){
 const db=await database();return new Promise<void>((resolve,reject)=>{const tx=db.transaction('state','readwrite'),store=tx.objectStore('state');store.put(state,'current');if(clearDraft)store.delete('story-draft-v2');tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error);tx.onerror=()=>reject(tx.error)});
}
export async function saveMediaState(state:State,id:string,media:MediaBytes){
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
    try{tx.objectStore('blobs').put(media,id);tx.objectStore('state').put(state,'current')}
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
 const db=await database();return new Promise((resolve,reject)=>{
  const tx=db.transaction(['state','blobs']);let state=fallback,storyDraft:StoryDraft|undefined,pending=2;const blobs:StoredMedia[]=[];
  function readMedia(){if(--pending)return;try{for(const id of idsFor(state,storyDraft)){const request=tx.objectStore('blobs').get(id);request.onsuccess=()=>{const blob=mediaBlob(request.result);if(blob)blobs.push({id,blob})}}}catch(error){tx.abort();reject(error)}}
  const current=tx.objectStore('state').get('current');current.onsuccess=()=>{if(current.result)state=current.result;readMedia()};
  const draft=tx.objectStore('state').get('story-draft-v2');draft.onsuccess=()=>{storyDraft=draft.result||undefined;readMedia()};
  tx.oncomplete=()=>resolve({state,storyDraft,blobs});tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
 });
}
export async function commitImport(fallback:State,merge:(current:State,hasDraft:boolean)=>LocalSnapshot):Promise<State>{
 const db=await database();return new Promise((resolve,reject)=>{
  const tx=db.transaction(['state','blobs'],'readwrite');let next:State;
  const current=tx.objectStore('state').get('current'),draft=tx.objectStore('state').get('story-draft-v2');let pending=2;
  function apply(){if(--pending)return;try{
   const prepared=merge(current.result||fallback,!!draft.result);next=prepared.state;
   // Every remapped file and the state that refers to it commit together.
   for(const media of prepared.blobs)tx.objectStore('blobs').add(media.blob,media.id);
   tx.objectStore('state').put(next,'current');
   if(prepared.storyDraft&&!draft.result)tx.objectStore('state').put(prepared.storyDraft,'story-draft-v2');
  }catch(error){tx.abort();reject(error)}}
  current.onsuccess=apply;draft.onsuccess=apply;
  tx.oncomplete=()=>resolve(next!);tx.onabort=()=>reject(tx.error||Error('Восстановление прервано. Текущие данные сохранены.'));tx.onerror=()=>reject(tx.error);
 });
}
