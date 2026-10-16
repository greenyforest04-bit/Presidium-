import type {MarketAction,ListingInput} from './commissionka-schema';
import type {MarketFilters,MarketContext,ListingPage,Listing,MarketOrder} from './commissionka';
import {rublesToKopecks} from './commissionka';
import type {DraftPhoto} from './commissionka-local';
export class MarketplaceError extends Error{constructor(message:string,public status:number,public code?:string){super(message)}}
async function response<T>(r:Response):Promise<T>{if(!r.headers.get('content-type')?.includes('application/json'))throw new MarketplaceError('Сервис Комиссионки ещё не подключён или временно недоступен.',r.status,'catalog_unavailable');const body=await r.json() as T&{error?:string;code?:string};if(!r.ok)throw new MarketplaceError(body.error||'Комиссионка временно недоступна.',r.status,body.code);return body}
async function marketFetch<T>(input:RequestInfo|URL,init?:RequestInit){try{return await response<T>(await fetch(input,init))}catch(error){if(error instanceof TypeError)throw new MarketplaceError('Нет связи с сервисом Комиссионки. Проверьте интернет и повторите.',0,'network_error');throw error}}
export interface MarketplaceDataSource{context(signal?:AbortSignal):Promise<MarketContext>;catalog(filters:MarketFilters,cursor?:string,signal?:AbortSignal):Promise<ListingPage>;detail(id:string,signal?:AbortSignal):Promise<Listing>;favorites(signal?:AbortSignal):Promise<Listing[]>;own(signal?:AbortSignal):Promise<Listing[]>;orders(signal?:AbortSignal):Promise<MarketOrder[]>;favorite(id:string,saved:boolean):Promise<void>;create(input:ListingInput,signal?:AbortSignal):Promise<{listing:Listing}>}
export class MarketplaceApi implements MarketplaceDataSource{
 private get<T>(params:URLSearchParams,signal?:AbortSignal){return marketFetch<T>('/api/marketplace?'+params,{cache:'no-store',signal})}
 context(signal?:AbortSignal){return this.get<MarketContext>(new URLSearchParams({view:'context'}),signal)}
 catalog(f:MarketFilters,cursor?:string,signal?:AbortSignal){const p=new URLSearchParams({query:f.query,category:f.category,condition:f.condition,city:f.city,verified:String(f.verified),sellerType:f.sellerType,sort:f.sort});if(f.minPrice)p.set('min',String(rublesToKopecks(f.minPrice)));if(f.maxPrice)p.set('max',String(rublesToKopecks(f.maxPrice)));if(cursor)p.set('cursor',cursor);return this.get<ListingPage>(p,signal)}
 detail(id:string,signal?:AbortSignal){return this.get<Listing>(new URLSearchParams({view:'detail',id}),signal)}
 favorites(signal?:AbortSignal){return this.get<{listings:Listing[]}>(new URLSearchParams({view:'favorites'}),signal).then(x=>x.listings)}
 own(signal?:AbortSignal){return this.get<{listings:Listing[]}>(new URLSearchParams({view:'own'}),signal).then(x=>x.listings)}
 orders(signal?:AbortSignal){return this.get<{orders:MarketOrder[]}>(new URLSearchParams({view:'orders'}),signal).then(x=>x.orders)}
 async favorite(id:string,saved:boolean){await this.act({action:'favorite',listingId:id,saved})}
 create(input:ListingInput,signal?:AbortSignal){return this.act<{listing:Listing}>(input,signal)}
 act<T>(action:MarketAction,signal?:AbortSignal){return marketFetch<T>('/api/marketplace',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(action),signal})}
 async upload(photo:DraftPhoto,signal?:AbortSignal){for(const [variant,blob] of [['full',photo.blob],['thumb',photo.thumbnail]] as const){await marketFetch('/api/marketplace/media?'+new URLSearchParams({id:photo.id,variant}),{method:'POST',headers:{'Content-Type':blob.type},body:blob,signal})}}
}
export const marketplaceApi=new MarketplaceApi();
