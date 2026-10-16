import {database,read,scopeKey,blobKey} from './storage';
import {emptyFilters,type Listing,type ListingDraft,type MarketFilters} from './commissionka';
export type MarketLocal={favorites:string[];savedListings:Listing[];filters:MarketFilters;source:'server'|'fixture';scrollTop:number;draft?:ListingDraft};
export const defaultMarketLocal:MarketLocal={favorites:[],savedListings:[],filters:emptyFilters,source:'server',scrollTop:0};
const key=(scope:string)=>'commissionka-v1:'+scope;
export type DraftPhoto={id:string;blob:Blob;thumbnail:Blob};
export async function readMarketLocal(scope:string){const stored=await read<MarketLocal>('state',key(scope));return {...defaultMarketLocal,...stored,filters:{...emptyFilters,...stored?.filters}}}
/** One transaction serializes preference/draft writes and attaches all photos. */
export async function updateMarketLocal(scope:string,update:(value:MarketLocal)=>MarketLocal,photos:DraftPhoto[]=[]){
 const storageKey=scopeKey(key(scope)),mediaKey=blobKey('');const owned=(id:string)=>mediaKey.replace('blob:', 'blob:'+id);const db=await database();return new Promise<MarketLocal>((resolve,reject)=>{
  const tx=db.transaction(['state','blobs'],'readwrite'),store=tx.objectStore('state'),request=store.get(storageKey);let next:MarketLocal,problem:unknown;
  request.onsuccess=()=>{try{const previous={...defaultMarketLocal,...request.result,filters:{...emptyFilters,...request.result?.filters}};next=update(previous);store.put(next,storageKey);for(const photo of photos){tx.objectStore('blobs').put(photo.blob,owned('commissionka-photo:'+scope+':'+photo.id));tx.objectStore('blobs').put(photo.thumbnail,owned('commissionka-thumb:'+scope+':'+photo.id))}const retained=new Set(next.draft?.photoIds||[]);for(const id of previous.draft?.photoIds||[])if(!retained.has(id)){tx.objectStore('blobs').delete(owned('commissionka-photo:'+scope+':'+id));tx.objectStore('blobs').delete(owned('commissionka-thumb:'+scope+':'+id))}}catch(error){problem=error;tx.abort()}};
  tx.oncomplete=()=>resolve(next!);tx.onabort=()=>reject(problem||tx.error||Error('Черновик не сохранён.'));tx.onerror=()=>reject(tx.error);
 });
}
export async function readDraftPhoto(scope:string,id:string):Promise<DraftPhoto>{const blob=await read<Blob>('blobs','commissionka-photo:'+scope+':'+id),thumbnail=await read<Blob>('blobs','commissionka-thumb:'+scope+':'+id);if(!blob||!thumbnail)throw Error('Фотография не найдена на устройстве. Добавьте её заново.');return {id,blob,thumbnail}}
