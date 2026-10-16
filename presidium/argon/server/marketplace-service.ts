import {AccountError,readAccountJson} from '../lib/account';
import {AccountService} from './account-service';
import {requireFeature,subscription} from './subscription-service';
import {CATEGORIES,categoryName,MAX_PRICE_KOPECKS,feeBreakdown,confirmedOrderBreakdown,type Listing,type ListingPage,type MarketContext,type VatPolicy,type Verification,type SellerType,type Condition,type Category,type OrderStatus,type MarketOrder,type MarketDispute,type ServerOrderTerms} from '../lib/commissionka';
import type {ListingInput,MarketAction} from '../lib/commissionka-schema';
type Row={id:string;seller_id:string;category:Category;condition:Condition;seller_type:SellerType;title:string;description:string;city:string;price_kopecks:number;specs:string;photo_ids:string;status:Listing['status'];condition_verified:number;active_order_id?:string|null;revision:number;created_at:number;updated_at:number;request_digest:string;name:string;identity:Verification|null;ownership:Verification|null;right_to_sell:Verification|null;manufacturer:Verification|null;matrix_user_id:string|null;matrix_base_url:string|null};
type OrderRow={id:string;listing_id:string;buyer_id:string;seller_id:string;locked_revision:number;locked_price_kopecks:number;commission_bps:200|500;commission_kopecks:number;vat_policy:'none'|'included_22';vat_kopecks:number;platform_net_kopecks:number;delivery_kopecks:number;payment_operator_kopecks:number;buyer_total_kopecks:number;seller_net_kopecks:number;status:OrderStatus;payment_mode:'sandbox'|'production';idempotency_key:string;created_at:number;updated_at:number;listing_title?:string;listing_city?:string;listing_category?:Category;listing_photos?:string;seller_name?:string;matrix_user_id?:string|null;matrix_base_url?:string|null};
type DisputeRow={id:string;order_id:string;initiator_id:string;reason:string;status:MarketDispute['status'];resolution_note:string;resolved_by:string|null;created_at:number;updated_at:number};
const select=`SELECT l.*,p.name,s.identity,s.manufacturer,s.matrix_user_id,s.matrix_base_url FROM market_listings l JOIN profiles p ON p.user_id=l.seller_id LEFT JOIN market_sellers s ON s.user_id=l.seller_id`;
const selectOrder=`SELECT o.*,l.title AS listing_title,l.city AS listing_city,l.category AS listing_category,l.photo_ids AS listing_photos,p.name AS seller_name,s.matrix_user_id,s.matrix_base_url FROM market_orders o LEFT JOIN market_listings l ON l.id=o.listing_id LEFT JOIN profiles p ON p.user_id=o.seller_id LEFT JOIN market_sellers s ON s.user_id=o.seller_id`;
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
export const validListingId=(id:string)=>uuid.test(id);
export type MarketConfig={
 COMMISSIONKA_VAT_MODE?:string;
 COMMISSIONKA_MATRIX_BASE_URL?:string;
 COMMISSIONKA_SANDBOX_ORDERS?:string;
 COMMISSIONKA_DELIVERY_KOPECKS?:string;
 COMMISSIONKA_PAYMENT_OPERATOR_KOPECKS?:string;
 PRESIDIUM_MODERATOR_IDS?:string;
};
export function marketVat(config:MarketConfig):VatPolicy{return config.COMMISSIONKA_VAT_MODE==='included_22'?'included_22':config.COMMISSIONKA_VAT_MODE==='none'?'none':'unknown'}
export function parseServerKopecks(value?:string):number|null{
 if(typeof value!=='string'||!/^\d+$/.test(value.trim()))return null;
 const n=Number(value.trim());
 return Number.isSafeInteger(n)&&n>=0&&n<=MAX_PRICE_KOPECKS?n:null;
}
export function serverOrderTerms(config:MarketConfig):ServerOrderTerms{
 return {
  vatPolicy:marketVat(config),
  deliveryKopecks:parseServerKopecks(config.COMMISSIONKA_DELIVERY_KOPECKS),
  paymentOperatorKopecks:parseServerKopecks(config.COMMISSIONKA_PAYMENT_OPERATOR_KOPECKS),
 };
}
export function isCheckoutReady(config:MarketConfig):boolean{
 const terms=serverOrderTerms(config);
 return config.COMMISSIONKA_SANDBOX_ORDERS==='1'&&terms.vatPolicy!=='unknown'&&terms.deliveryKopecks!==null&&terms.paymentOperatorKopecks!==null;
}
export function isModerator(userId:string,config:MarketConfig):boolean{
 if(!userId||!config.PRESIDIUM_MODERATOR_IDS)return false;
 return config.PRESIDIUM_MODERATOR_IDS.split(',').map(s=>s.trim()).filter(Boolean).includes(userId);
}
export function matrixBase(config:MarketConfig){try{if(!config.COMMISSIONKA_MATRIX_BASE_URL)return null;const url=new URL(config.COMMISSIONKA_MATRIX_BASE_URL);if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||url.hostname==='localhost'||url.hostname.endsWith('.local')||/^[\d.]+$/.test(url.hostname)||url.hostname.includes(':'))return null;return url.href.replace(/\/$/,'')}catch{return null}}
async function digest(value:string){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),n=>n.toString(16).padStart(2,'0')).join('')}
export class MarketplaceService{
 constructor(private db:D1Database,private owner:string,private token:string|undefined,private config:MarketConfig={},private hasMedia=false){}
 private async authorized(paid=false){const session=await new AccountService(this.db,this.owner).requireSession(this.token);if(paid)await requireFeature(this.db,this.owner,'marketplace');return session}
 private async requireModerator(){
  const session=await this.authorized(false);
  if(!isModerator(this.owner,this.config))throw new AccountError('Действие доступно только назначенному модератору платформы. Подписка или владение объявлением не дают прав модерации.',403,'moderator_required');
  return session;
 }
 private guard(paid:boolean){return `EXISTS(SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL AND expires_at>?)${paid?" AND EXISTS(SELECT 1 FROM subscriptions WHERE user_id=? AND status='active' AND tier IN ('plus','business') AND period_start<=? AND period_end>?)":''}`}
 private args(sessionId:string,paid:boolean,now:number){return [sessionId,this.owner,now,...(paid?[this.owner,now,now]:[])]}
 async context():Promise<MarketContext>{
  const session=await new AccountService(this.db,this.owner).session(this.token),plan=session?await subscription(this.db,this.owner):{tier:'free' as const,verified:false},favorites=session?await this.db.prepare('SELECT listing_id FROM market_favorites WHERE user_id=? LIMIT 200').bind(this.owner).all<{listing_id:string}>():null;
  const ready=isCheckoutReady(this.config),terms=serverOrderTerms(this.config);
  return {owner:this.owner,sessionActive:!!session,subscription:plan,favoriteIds:favorites?.results.map(x=>x.listing_id)||[],capabilities:{catalog:true,publish:this.hasMedia,checkout:ready,sandboxCheckout:ready,moderation:!!session&&isModerator(this.owner,this.config),verification:false,matrixBaseUrl:matrixBase(this.config)},vatPolicy:terms.vatPolicy,orderTerms:terms};
 }
 private listing(row:Row,history:Listing['priceHistory']=[]):Listing{
  const ids=JSON.parse(row.photo_ids) as string[];
  return {id:row.id,title:row.title,description:row.description,category:row.category,condition:row.condition,priceKopecks:row.price_kopecks,city:row.city,specs:Object.fromEntries((JSON.parse(row.specs) as {name:string;value:string}[]).map(x=>[x.name,x.value])),images:ids.map(id=>({id,url:'/api/marketplace/media?id='+id+'&variant=full',thumbnail:'/api/marketplace/media?id='+id+'&variant=thumb'})),seller:{id:row.seller_id,name:row.name,type:row.seller_type,identity:row.identity||'unverified',ownership:row.ownership||'unverified',rightToSell:row.right_to_sell||'unverified',manufacturer:row.manufacturer||'unverified',...(row.matrix_user_id&&row.matrix_base_url?{matrix:{userId:row.matrix_user_id,baseUrl:row.matrix_base_url}}:{})},status:row.status,conditionVerified:!!row.condition_verified,revision:row.revision,createdAt:row.created_at,updatedAt:row.updated_at,priceHistory:history,source:'server'};
 }
 private formatDispute(d:DisputeRow):MarketDispute{
  return {id:d.id,orderId:d.order_id,initiatorId:d.initiator_id,reason:d.reason,status:d.status,resolutionNote:d.resolution_note,resolvedBy:d.resolved_by,createdAt:d.created_at,updatedAt:d.updated_at};
 }
 private formatOrder(row:OrderRow,dispute?:DisputeRow|null):MarketOrder{
  const photos=row.listing_photos?(JSON.parse(row.listing_photos) as string[]):[];
  return {
   id:row.id,
   listingId:row.listing_id,
   listingTitle:row.listing_title,
   listingCity:row.listing_city,
   listingCategory:row.listing_category,
   listingThumbnail:photos[0]?'/api/marketplace/media?id='+photos[0]+'&variant=thumb':undefined,
   buyerId:row.buyer_id,
   sellerId:row.seller_id,
   sellerName:row.seller_name,
   sellerMatrix:row.matrix_user_id&&row.matrix_base_url?{userId:row.matrix_user_id,baseUrl:row.matrix_base_url}:undefined,
   lockedRevision:row.locked_revision,
   lockedPriceKopecks:row.locked_price_kopecks,
   financials:{
    priceKopecks:row.locked_price_kopecks,
    commissionBps:row.commission_bps,
    commissionKopecks:row.commission_kopecks,
    vatPolicy:row.vat_policy,
    vatKopecks:row.vat_kopecks,
    platformNetKopecks:row.platform_net_kopecks,
    deliveryKopecks:row.delivery_kopecks,
    paymentOperatorKopecks:row.payment_operator_kopecks,
    buyerTotalKopecks:row.buyer_total_kopecks,
    sellerNetKopecks:row.seller_net_kopecks,
   },
   status:row.status,
   paymentMode:row.payment_mode,
   idempotencyKey:row.idempotency_key,
   dispute:dispute?this.formatDispute(dispute):undefined,
   createdAt:row.created_at,
   updatedAt:row.updated_at,
  };
 }
 private async loadOrder(orderId:string):Promise<MarketOrder>{
  const row=await this.db.prepare(selectOrder+' WHERE o.id=?').bind(orderId).first<OrderRow>();
  if(!row)throw new AccountError('Заказ не найден.',404);
  const dispute=await this.db.prepare('SELECT * FROM market_disputes WHERE order_id=? ORDER BY created_at DESC LIMIT 1').bind(orderId).first<DisputeRow>();
  return this.formatOrder(row,dispute);
 }
 async detail(id:string){
  if(!validListingId(id))throw new AccountError('Объявление не найдено.',404,'listing_missing');
  const mod=isModerator(this.owner,this.config);
  const row=await this.db.prepare(select+" WHERE l.id=? AND (l.status IN ('active','sold') OR l.seller_id=? OR ?=1)").bind(id,this.owner,mod?1:0).first<Row>();
  if(!row)throw new AccountError('Объявление удалено или недоступно.',404,'listing_missing');
  if(!['active','sold'].includes(row.status)){if(mod)await this.requireModerator();else await this.authorized()}
  const history=await this.db.prepare('SELECT price_kopecks AS priceKopecks,at,revision FROM market_price_history WHERE listing_id=? ORDER BY revision DESC LIMIT 20').bind(id).all<Listing['priceHistory'][number]>();
  return this.listing(row,history.results);
 }
 async catalog(params:URLSearchParams):Promise<ListingPage>{
  const where=["l.status='active'"],args:(string|number)[]=[],query=(params.get('query')||'').trim().slice(0,120).toLocaleLowerCase('ru-RU'),city=(params.get('city')||'').trim().slice(0,80).toLocaleLowerCase('ru-RU');
  const escaped=(value:string)=>'%'+value.replace(/[\%_]/g,'\$&')+'%';
  if(query){where.push("l.search_text LIKE ? ESCAPE '\\'");args.push(escaped(query))}
  if(city){where.push("l.city_search LIKE ? ESCAPE '\\'");args.push(escaped(city))}
  for(const [param,column,values] of [['category','l.category',CATEGORIES.map(([id])=>id)],['condition','l.condition',['new','used']],['sellerType','l.seller_type',['private','company','manufacturer']]] as const){const value=params.get(param);if(value){if(!(values as readonly string[]).includes(value))throw new AccountError('Некорректный фильтр.',400);where.push(column+'=?');args.push(value)}}
  for(const [param,comparison] of [['min','>='],['max','<=']] as const){const v=params.get(param);if(v!==null){const n=Number(v);if(!/^\d+$/.test(v)||!Number.isSafeInteger(n)||n<0||n>MAX_PRICE_KOPECKS)throw new AccountError('Некорректный диапазон цен.',400);where.push('l.price_kopecks'+comparison+'?');args.push(n)}}
  if(params.get('min')&&params.get('max')&&Number(params.get('min'))>Number(params.get('max')))throw new AccountError('Минимальная цена выше максимальной.',400);
  if(params.get('verified')==='true')where.push("s.identity='verified'");
  const sort=params.get('sort')||'relevant';if(!['relevant','newest','price-asc','price-desc'].includes(sort))throw new AccountError('Некорректная сортировка.',400);
  const field=sort.startsWith('price')?'l.price_kopecks':'l.created_at',ascending=sort==='price-asc',operator=ascending?'>':'<',direction=ascending?'ASC':'DESC',baseWhere=where.join(' AND '),baseArgs=[...args];
  const cursor=params.get('cursor');if(cursor){try{if(cursor.length>300)throw Error();const c=JSON.parse(atob(cursor));if(c.sort!==sort||!validListingId(c.id)||!Number.isSafeInteger(c.key)||c.key<0)throw Error();where.push(`(${field}${operator}? OR (${field}=? AND l.id${operator}?))`);args.push(c.key,c.key,c.id)}catch{throw new AccountError('Не удалось прочитать страницу каталога.',400)}}
  const [rows,count]=await Promise.all([this.db.prepare(select+' WHERE '+where.join(' AND ')+` ORDER BY ${field} ${direction},l.id ${direction} LIMIT 25`).bind(...args).all<Row>(),this.db.prepare('SELECT COUNT(*) AS total FROM market_listings l LEFT JOIN market_sellers s ON s.user_id=l.seller_id WHERE '+baseWhere).bind(...baseArgs).first<{total:number}>()]);
  const page=rows.results.slice(0,24),last=page.at(-1),nextCursor=rows.results.length>24&&last?btoa(JSON.stringify({sort,key:sort.startsWith('price')?last.price_kopecks:last.created_at,id:last.id})):null;
  return {listings:page.map(x=>this.listing(x)),total:count?.total||0,nextCursor};
 }
 async favorites(){await this.authorized();const rows=await this.db.prepare(select+" JOIN market_favorites f ON f.listing_id=l.id WHERE f.user_id=? AND l.status IN ('active','sold','removed') ORDER BY f.created_at DESC LIMIT 200").bind(this.owner).all<Row>();return {listings:rows.results.map(x=>this.listing(x))}}
 async own(){await this.authorized();const rows=await this.db.prepare(select+' WHERE l.seller_id=? ORDER BY l.updated_at DESC LIMIT 100').bind(this.owner).all<Row>();return {listings:rows.results.map(x=>this.listing(x))}}
 async moderationQueue(){
  await this.requireModerator();
  const listings=await this.db.prepare("SELECT id FROM market_listings WHERE status='pending' ORDER BY created_at LIMIT 100").all<{id:string}>();
  const orders=await this.db.prepare("SELECT id FROM market_orders WHERE status='disputed' ORDER BY updated_at LIMIT 100").all<{id:string}>();
  return {listings:await Promise.all(listings.results.map(r=>this.detail(r.id))),orders:await Promise.all(orders.results.map(r=>this.loadOrder(r.id)))};
 }
 async orders(){
  await this.authorized();
  const rows=await this.db.prepare(selectOrder+' WHERE o.buyer_id=? OR o.seller_id=? ORDER BY o.created_at DESC LIMIT 100').bind(this.owner,this.owner).all<OrderRow>();
  if(!rows.results.length)return {orders:[]};
  const orderIds=rows.results.map(r=>r.id);
  const disputes=await this.db.prepare(`SELECT * FROM market_disputes WHERE order_id IN (${orderIds.map(()=>'?').join(',')}) ORDER BY created_at DESC`).bind(...orderIds).all<DisputeRow>();
  const byOrder=new Map<string,DisputeRow>();
  for(const d of disputes.results)if(!byOrder.has(d.order_id))byOrder.set(d.order_id,d);
  return {orders:rows.results.map(r=>this.formatOrder(r,byOrder.get(r.id)))};
 }
 async create(input:ListingInput){
  const session=await this.authorized(true);if(!this.hasMedia)throw new AccountError('Хранилище фотографий ещё не подключено. Черновик остаётся на устройстве.',503,'media_unavailable');
  const requestDigest=await digest(JSON.stringify(input)),existing=await this.db.prepare('SELECT seller_id,request_digest FROM market_listings WHERE id=?').bind(input.id).first<{seller_id:string;request_digest:string}>();if(existing){if(existing.seller_id!==this.owner||existing.request_digest!==requestDigest)throw new AccountError('Идентификатор объявления уже использован.',409);return {listing:await this.detail(input.id)}}
  const photoRows=await this.db.prepare(`SELECT id FROM market_images WHERE owner_id=? AND listing_id IS NULL AND thumb_digest IS NOT NULL AND id IN (${input.photoIds.map(()=>'?').join(',')})`).bind(this.owner,...input.photoIds).all<{id:string}>();if(photoRows.results.length!==input.photoIds.length)throw new AccountError('Загрузите все фотографии этого объявления.',409,'photos_missing');
  const count=await this.db.prepare("SELECT COUNT(*) AS n FROM market_listings WHERE seller_id=? AND status<>'removed'").bind(this.owner).first<{n:number}>();if((count?.n||0)>=200)throw new AccountError('Сначала завершите или снимите прежние объявления.',429);
  const now=Date.now(),guard=this.guard(true),args=this.args(session.id,true,now),owned=`(SELECT COUNT(*) FROM market_images WHERE owner_id=? AND listing_id IS NULL AND thumb_digest IS NOT NULL AND id IN (${input.photoIds.map(()=>'?').join(',')}))=?`;
  await this.db.batch([
   this.db.prepare(`INSERT INTO market_listings(id,seller_id,category,condition,seller_type,title,description,city,search_text,city_search,price_kopecks,specs,photo_ids,status,created_at,updated_at,request_digest) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,? WHERE ${guard} AND ${owned} AND (SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND status<>'removed')<200 ON CONFLICT(id) DO NOTHING`).bind(input.id,this.owner,input.category,input.condition,input.sellerType,input.title,input.description,input.city,`${input.title} ${categoryName(input.category)} ${input.city}`.toLocaleLowerCase('ru-RU'),input.city.toLocaleLowerCase('ru-RU'),input.priceKopecks,JSON.stringify(input.specs),JSON.stringify(input.photoIds),now,now,requestDigest,...args,this.owner,...input.photoIds,input.photoIds.length,this.owner),
   this.db.prepare(`UPDATE market_images SET listing_id=? WHERE owner_id=? AND listing_id IS NULL AND id IN (${input.photoIds.map(()=>'?').join(',')}) AND EXISTS(SELECT 1 FROM market_listings WHERE id=? AND seller_id=? AND request_digest=?)`).bind(input.id,this.owner,...input.photoIds,input.id,this.owner,requestDigest),
   this.db.prepare('INSERT INTO market_price_history(listing_id,revision,price_kopecks,at) SELECT id,revision,price_kopecks,created_at FROM market_listings WHERE id=? AND seller_id=? AND request_digest=? ON CONFLICT(listing_id,revision) DO NOTHING').bind(input.id,this.owner,requestDigest),
  ]);
  const saved=await this.db.prepare('SELECT seller_id,request_digest FROM market_listings WHERE id=?').bind(input.id).first<{seller_id:string;request_digest:string}>();if(!saved||saved.seller_id!==this.owner||saved.request_digest!==requestDigest){await this.authorized(true);throw new AccountError('Фотографии уже использованы другим объявлением. Проверьте черновик.',409)}return {listing:await this.detail(input.id)};
 }
 async act(action:MarketAction){
  if(action.action==='create')return this.create(action);
  if(action.action==='moderateListing'){
   await this.requireModerator();
   const now=Date.now(),listing=await this.detail(action.listingId);
   if(listing.status==='sold')throw new AccountError('Проданный товар нельзя модерировать.',409);
   const nextStatus=action.decision==='approve'?'active':'removed';
   const condVer=action.decision==='approve'&&action.conditionVerified?1:0;
   const rightVer=action.decision==='approve'&&action.rightToSellVerified?'verified':'unverified';
   const res=await this.db.batch([
    this.db.prepare("UPDATE market_listings SET status=?,condition_verified=?,right_to_sell=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND status<>'sold' AND active_order_id IS NULL").bind(nextStatus,condVer,rightVer,now,listing.id,action.revision),
    this.db.prepare('INSERT INTO market_moderation_log(id,listing_id,moderator_id,decision,note,created_at) SELECT ?,?,?,?,?,? WHERE (SELECT changes())=1 AND EXISTS(SELECT 1 FROM market_listings WHERE id=? AND revision=? AND updated_at=?)').bind(crypto.randomUUID(),listing.id,this.owner,action.decision,action.note||'',now,listing.id,action.revision+1,now),
   ]);
   if(!res[0].meta.changes)throw new AccountError('Объявление изменилось во время проверки. Обновите карточку.',409,'revision_conflict');
   return {listing:await this.detail(listing.id)};
  }
  if(action.action==='resolveDispute'){
   await this.requireModerator();
   const now=Date.now();
   const dispute=await this.db.prepare('SELECT * FROM market_disputes WHERE id=?').bind(action.disputeId).first<DisputeRow>();
   if(!dispute)throw new AccountError('Спор не найден.',404);
   if(dispute.status!=='open')throw new AccountError('Спор уже закрыт.',409,'dispute_already_closed');
   const order=await this.db.prepare('SELECT * FROM market_orders WHERE id=?').bind(dispute.order_id).first<OrderRow>();
   if(!order)throw new AccountError('Связанный заказ не найден.',404);
   const disputeStatus=action.decision==='refund_buyer'?'resolved_refund':action.decision==='release_seller'?'resolved_release':'dismissed';
   const orderStatus:OrderStatus=action.decision==='refund_buyer'?'cancelled':'completed';
   const stmts=[
    this.db.prepare("UPDATE market_disputes SET status=?,resolution_note=?,resolved_by=?,updated_at=? WHERE id=? AND status='open'").bind(disputeStatus,action.resolutionNote,this.owner,now,dispute.id),
    this.db.prepare("UPDATE market_orders SET status=?,updated_at=? WHERE id=? AND status='disputed' AND EXISTS(SELECT 1 FROM market_disputes WHERE id=? AND status=? AND resolved_by=?)").bind(orderStatus,now,order.id,dispute.id,disputeStatus,this.owner),
   ];
   if(action.decision==='refund_buyer'){
    stmts.push(this.db.prepare("UPDATE market_listings SET status='active',active_order_id=NULL,revision=revision+1,updated_at=? WHERE id=? AND status='sold' AND active_order_id=? AND EXISTS(SELECT 1 FROM market_orders WHERE id=? AND status='cancelled')").bind(now,order.listing_id,order.id,order.id));
   }
   const res=await this.db.batch(stmts);
   if(!res[0].meta.changes||!res[1].meta.changes)throw new AccountError('Спор уже был закрыт или статус заказа изменился.',409,'concurrent_status_change');
   return {order:await this.loadOrder(order.id)};
  }
  const paid=!['favorite','unlinkMatrix','withdraw','orderStatus','dispute','paySandbox'].includes(action.action),session=await this.authorized(paid),now=Date.now(),guard=this.guard(paid),args=this.args(session.id,paid,now);
  if(action.action==='unlinkMatrix'){await this.db.prepare(`UPDATE market_sellers SET matrix_user_id=NULL,matrix_base_url=NULL WHERE user_id=? AND ${guard}`).bind(this.owner,...args).run();await this.authorized();return {unlinked:true}}
  if(action.action==='favorite'){
   if(!action.saved){await this.db.prepare(`DELETE FROM market_favorites WHERE user_id=? AND listing_id=? AND ${guard}`).bind(this.owner,action.listingId,...args).run();return {saved:false}}
   const result=await this.db.prepare(`INSERT INTO market_favorites(user_id,listing_id,created_at) SELECT ?,?,? WHERE ${guard} AND EXISTS(SELECT 1 FROM market_listings WHERE id=? AND status IN ('active','sold')) AND (SELECT COUNT(*) FROM market_favorites WHERE user_id=?)<200 ON CONFLICT(user_id,listing_id) DO NOTHING`).bind(this.owner,action.listingId,now,...args,action.listingId,this.owner).run();
   if(!result.meta.changes&&!await this.db.prepare('SELECT 1 FROM market_favorites WHERE user_id=? AND listing_id=?').bind(this.owner,action.listingId).first()){await this.authorized();throw new AccountError('Не удалось сохранить. Объявление недоступно или избранное заполнено.',409)}return {saved:true};
  }
  if(action.action==='linkMatrix'){
   const base=matrixBase(this.config);if(!base)throw new AccountError('Связывание Matrix-контакта ещё не настроено.',503,'matrix_unavailable');
   const response=await fetch(base+'/_matrix/client/v3/account/whoami',{headers:{Authorization:'Bearer '+action.accessToken},redirect:'error',signal:AbortSignal.timeout(10000)});if(!response.ok)throw new AccountError('Matrix-сеанс не подтверждён.',401);const body=await readAccountJson(response as unknown as Request) as {user_id?:string};if(!body.user_id||!/^@[^\s:]+:[^\s/]+$/.test(body.user_id)||body.user_id.length>255)throw new AccountError('Matrix вернул некорректный контакт.',502);
   const result=await this.db.prepare(`INSERT INTO market_sellers(user_id,matrix_user_id,matrix_base_url) SELECT ?,?,? WHERE ${guard} ON CONFLICT(user_id) DO UPDATE SET matrix_user_id=excluded.matrix_user_id,matrix_base_url=excluded.matrix_base_url`).bind(this.owner,body.user_id,base,...args).run();if(!result.meta.changes){await this.authorized(true);throw new AccountError('Контакт не привязан. Повторите запрос.',409)}return {linked:true};
  }
  if(action.action==='paySandbox'){
   if(this.config.COMMISSIONKA_SANDBOX_ORDERS!=='1')throw new AccountError('Тестовая оплата отключена в конфигурации сервера.',503,'sandbox_disabled');
   const order=await this.db.prepare('SELECT * FROM market_orders WHERE id=?').bind(action.orderId).first<OrderRow>();
   if(!order)throw new AccountError('Заказ не найден.',404);
   if(order.buyer_id!==this.owner)throw new AccountError('Подтвердить тестовую оплату может только покупатель этого заказа.',403,'forbidden_buyer');
   if(order.status==='paid_sandbox'||order.status==='paid')return {order:await this.loadOrder(order.id),idempotent:true};
   if(order.status!=='confirmed')throw new AccountError('Статус заказа не позволяет выполнить оплату.',409,'invalid_order_status');
   const res=await this.db.prepare(`UPDATE market_orders SET status='paid_sandbox',updated_at=? WHERE id=? AND buyer_id=? AND status='confirmed' AND ${guard}`).bind(now,order.id,this.owner,...args).run();
   if(!res.meta.changes){
    const fresh=await this.db.prepare('SELECT * FROM market_orders WHERE id=?').bind(order.id).first<OrderRow>();
    if(fresh&&(fresh.status==='paid_sandbox'||fresh.status==='paid'))return {order:await this.loadOrder(order.id),idempotent:true};
    throw new AccountError('Статус заказа изменился параллельным запросом и не позволяет выполнить оплату.',409,'concurrent_status_change');
   }
   return {order:await this.loadOrder(order.id),idempotent:false};
  }
  if(action.action==='orderStatus'){
   const order=await this.db.prepare('SELECT * FROM market_orders WHERE id=?').bind(action.orderId).first<OrderRow>();
   if(!order)throw new AccountError('Заказ не найден.',404);
   if(action.status==='shipped'){
    if(order.seller_id!==this.owner)throw new AccountError('Отметить отправку может только продавец.',403,'forbidden_seller');
    const res=await this.db.prepare(`UPDATE market_orders SET status='shipped',updated_at=? WHERE id=? AND seller_id=? AND status IN ('paid_sandbox','paid') AND ${guard}`).bind(now,order.id,this.owner,...args).run();
    if(!res.meta.changes)throw new AccountError('Отправка невозможна: статус заказа уже изменился (например, заказ отменён, уже отправлен или открыт спор).',409,'concurrent_status_change');
    return {order:await this.loadOrder(order.id)};
   }
   if(action.status==='completed'){
    if(order.buyer_id!==this.owner)throw new AccountError('Подтвердить получение может только покупатель.',403,'forbidden_buyer');
    const res=await this.db.prepare(`UPDATE market_orders SET status='completed',updated_at=? WHERE id=? AND buyer_id=? AND status='shipped' AND ${guard}`).bind(now,order.id,this.owner,...args).run();
    if(!res.meta.changes)throw new AccountError('Подтвердить получение можно только для отправленного заказа без открытого спора.',409,'concurrent_status_change');
    return {order:await this.loadOrder(order.id)};
   }
   // action.status === 'cancelled'
   if(order.buyer_id!==this.owner&&order.seller_id!==this.owner)throw new AccountError('Нет доступа к заказу.',403);
   const res=await this.db.batch([
    this.db.prepare(`UPDATE market_orders SET status='cancelled',updated_at=? WHERE id=? AND (buyer_id=? OR seller_id=?) AND status IN ('confirmed','paid_sandbox') AND ${guard}`).bind(now,order.id,this.owner,this.owner,...args),
    this.db.prepare("UPDATE market_listings SET status='active',active_order_id=NULL,revision=revision+1,updated_at=? WHERE id=? AND status='sold' AND active_order_id=? AND EXISTS(SELECT 1 FROM market_orders WHERE id=? AND status='cancelled')").bind(now,order.listing_id,order.id,order.id),
   ]);
   if(!res[0].meta.changes)throw new AccountError('Отменить заказ на этом этапе нельзя (товар уже отправлен, завершён или открыт спор).',409,'concurrent_status_change');
   return {order:await this.loadOrder(order.id)};
  }
  if(action.action==='dispute'){
   const order=await this.db.prepare('SELECT * FROM market_orders WHERE id=?').bind(action.orderId).first<OrderRow>();
   if(!order||(order.buyer_id!==this.owner&&order.seller_id!==this.owner))throw new AccountError('Заказ не найден или недоступен.',404);
   const existing=await this.db.prepare("SELECT * FROM market_disputes WHERE order_id=? AND status='open' LIMIT 1").bind(order.id).first<DisputeRow>();
   if(existing){if(existing.initiator_id===this.owner&&existing.reason===action.reason)return {order:await this.loadOrder(order.id),dispute:this.formatDispute(existing),idempotent:true};throw new AccountError('По этому заказу уже открыт активный спор.',409,'dispute_already_open');}
   if(!['paid_sandbox','paid','shipped'].includes(order.status))throw new AccountError('Спор можно открыть только по оплаченному или отправленному заказу.',409,'invalid_order_status');
   const disputeId=crypto.randomUUID();
   try{
    const res=await this.db.batch([
     this.db.prepare(`UPDATE market_orders SET status='disputed',updated_at=? WHERE id=? AND (buyer_id=? OR seller_id=?) AND status IN ('paid_sandbox','paid','shipped') AND ${guard}`).bind(now,order.id,this.owner,this.owner,...args),
     this.db.prepare("INSERT INTO market_disputes(id,order_id,initiator_id,reason,status,resolution_note,created_at,updated_at) SELECT ?,?,?,?,'open','',?,? WHERE (SELECT changes())=1 AND EXISTS(SELECT 1 FROM market_orders WHERE id=? AND status='disputed')").bind(disputeId,order.id,this.owner,action.reason,now,now,order.id),
    ]);
    if(!res[0].meta.changes||!res[1].meta.changes){
     const concurrentOpen=await this.db.prepare("SELECT * FROM market_disputes WHERE order_id=? AND status='open' LIMIT 1").bind(order.id).first<DisputeRow>();
     if(concurrentOpen){if(concurrentOpen.initiator_id===this.owner&&concurrentOpen.reason===action.reason)return {order:await this.loadOrder(order.id),dispute:this.formatDispute(concurrentOpen),idempotent:true};throw new AccountError('По этому заказу уже открыт активный спор.',409,'dispute_already_open');}
     throw new AccountError('Статус заказа изменился параллельным запросом и не позволяет открыть спор.',409,'concurrent_status_change');
    }
   }catch(error){
    if(error instanceof AccountError)throw error;
    const concurrentOpen=await this.db.prepare("SELECT * FROM market_disputes WHERE order_id=? AND status='open' LIMIT 1").bind(order.id).first<DisputeRow>();
    if(concurrentOpen){if(concurrentOpen.initiator_id===this.owner&&concurrentOpen.reason===action.reason)return {order:await this.loadOrder(order.id),dispute:this.formatDispute(concurrentOpen),idempotent:true};throw new AccountError('По этому заказу уже открыт активный спор.',409,'dispute_already_open');}
    throw new AccountError('Не удалось открыть спор: статус заказа изменился.',409,'concurrent_status_change');
   }
   const createdOrder=await this.loadOrder(order.id);
   return {order:createdOrder,dispute:createdOrder.dispute!,idempotent:false};
  }
  if(action.action==='checkout'){
   const verifyIdempotentOrder=async(existing:OrderRow)=>{
    if(existing.listing_id!==action.listingId||existing.locked_revision!==action.revision){
     throw new AccountError('Ключ идемпотентности уже использован для другого товара или другой ревизии объявления.',409,'idempotency_conflict');
    }
    return {order:await this.loadOrder(existing.id),idempotent:true};
   };
   if(action.idempotencyKey){
    const existingOrder=await this.db.prepare('SELECT * FROM market_orders WHERE buyer_id=? AND idempotency_key=?').bind(this.owner,action.idempotencyKey).first<OrderRow>();
    if(existingOrder)return verifyIdempotentOrder(existingOrder);
   }
   const listing=await this.detail(action.listingId);
   if(listing.seller.id===this.owner)throw new AccountError('Нельзя купить собственное объявление.',409,'own_listing');
   if(listing.status!=='active')throw new AccountError('Этот товар уже оформлен другим покупателем или недоступен.',409,'listing_unavailable');
   if(listing.revision!==action.revision)throw new AccountError('Цена или описание изменились. Обновите карточку товара перед покупкой.',409,'revision_conflict');
   if(this.config.COMMISSIONKA_SANDBOX_ORDERS!=='1'){
    const fees=feeBreakdown(listing.priceKopecks,listing.condition,marketVat(this.config));
    throw new AccountError(`Оформление заказа и оплата ещё не подключены. Цена товара ${fees.priceKopecks/100} ₽; заказ не создан.`,503,'checkout_unavailable');
   }
   const terms=serverOrderTerms(this.config);
   if(terms.vatPolicy==='unknown'||terms.deliveryKopecks===null||terms.paymentOperatorKopecks===null){
    throw new AccountError('Обязательные условия сделки (политика НДС, стоимость доставки или комиссия платёжного оператора) не определены на сервере. Заказ не создан.',503,'checkout_pricing_incomplete');
   }
   const fin=confirmedOrderBreakdown(listing.priceKopecks,listing.condition,terms);
   const orderId=crypto.randomUUID(),idemKey=action.idempotencyKey||crypto.randomUUID(),nextRev=listing.revision+1;
   try{
    const batchRes=await this.db.batch([
     this.db.prepare(`UPDATE market_listings SET status='sold',active_order_id=?,revision=?,updated_at=? WHERE id=? AND status='active' AND active_order_id IS NULL AND revision=? AND seller_id<>? AND NOT EXISTS(SELECT 1 FROM market_orders WHERE buyer_id=? AND idempotency_key=?) AND ${guard}`).bind(orderId,nextRev,now,listing.id,action.revision,this.owner,this.owner,idemKey,...args),
     this.db.prepare(`INSERT INTO market_orders(id,listing_id,buyer_id,seller_id,locked_revision,locked_price_kopecks,commission_bps,commission_kopecks,vat_policy,vat_kopecks,platform_net_kopecks,delivery_kopecks,payment_operator_kopecks,buyer_total_kopecks,seller_net_kopecks,status,payment_mode,idempotency_key,created_at,updated_at) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'confirmed','sandbox',?,?,? WHERE (SELECT changes())=1 AND EXISTS(SELECT 1 FROM market_listings WHERE id=? AND status='sold' AND active_order_id=? AND revision=?)`).bind(orderId,listing.id,this.owner,listing.seller.id,listing.revision,fin.priceKopecks,fin.commissionBps,fin.commissionKopecks,fin.vatPolicy,fin.vatKopecks,fin.platformNetKopecks,fin.deliveryKopecks,fin.paymentOperatorKopecks,fin.buyerTotalKopecks,fin.sellerNetKopecks,idemKey,now,now,listing.id,orderId,nextRev),
    ]);
    if(!batchRes[0].meta.changes||!batchRes[1].meta.changes){
     if(action.idempotencyKey){
      const idemWinner=await this.db.prepare('SELECT * FROM market_orders WHERE buyer_id=? AND idempotency_key=?').bind(this.owner,action.idempotencyKey).first<OrderRow>();
      if(idemWinner)return verifyIdempotentOrder(idemWinner);
     }
     const current=await this.db.prepare('SELECT status,revision,active_order_id FROM market_listings WHERE id=?').bind(listing.id).first<{status:string;revision:number;active_order_id:string|null}>();
     if(current&&current.status==='active'&&current.revision!==action.revision)throw new AccountError('Цена или условия объявления изменились. Обновите карточку.',409,'revision_conflict');
     throw new AccountError('Товар только что был оформлен другим покупателем или стал недоступен.',409,'listing_unavailable');
    }
   }catch(error){
    if(error instanceof AccountError)throw error;
    if(action.idempotencyKey){
     const idemWinner=await this.db.prepare('SELECT * FROM market_orders WHERE buyer_id=? AND idempotency_key=?').bind(this.owner,action.idempotencyKey).first<OrderRow>();
     if(idemWinner)return verifyIdempotentOrder(idemWinner);
    }
    throw new AccountError('Товар только что был оформлен другим покупателем.',409,'listing_unavailable');
   }
   return {order:await this.loadOrder(orderId),idempotent:false};
  }
  const listing=await this.detail(action.listingId);
  if(listing.seller.id!==this.owner)throw new AccountError('Изменять объявление может только его продавец.',403);
  if(action.action==='update'){
   if(listing.status==='sold'||listing.status==='removed')throw new AccountError('Завершённое объявление не редактируется.',409);
   const searchText=`${action.title} ${categoryName(action.category)} ${action.city}`.toLocaleLowerCase('ru-RU'),citySearch=action.city.toLocaleLowerCase('ru-RU');
   const result=await this.db.prepare(`UPDATE market_listings SET category=?,condition=?,title=?,description=?,city=?,search_text=?,city_search=?,specs=?,status='pending',condition_verified=0,revision=revision+1,updated_at=? WHERE id=? AND seller_id=? AND revision=? AND status IN ('pending','active') AND active_order_id IS NULL AND ${guard}`).bind(action.category,action.condition,action.title,action.description,action.city,searchText,citySearch,JSON.stringify(action.specs),now,listing.id,this.owner,action.revision,...args).run();
   if(!result.meta.changes)throw new AccountError('Объявление изменилось на другом устройстве. Загрузите актуальное состояние.',409,'revision_conflict');
   return {listing:await this.detail(listing.id)};
  }
  if(action.action==='withdraw'){
   if(listing.status==='sold')throw new AccountError('Проданный товар нельзя снять с публикации.',409);
   const result=await this.db.prepare(`UPDATE market_listings SET status='removed',revision=revision+1,updated_at=? WHERE id=? AND seller_id=? AND revision=? AND status<>'sold' AND active_order_id IS NULL AND ${guard}`).bind(now,listing.id,this.owner,action.revision,...args).run();
   if(!result.meta.changes)throw new AccountError('Объявление изменилось. Обновите его.',409,'revision_conflict');
   return {removed:true};
  }
  if(listing.status==='sold'||listing.status==='removed')throw new AccountError('Цена завершённого объявления не меняется.',409);
  const result=await this.db.batch([this.db.prepare(`UPDATE market_listings SET price_kopecks=?,revision=revision+1,updated_at=? WHERE id=? AND seller_id=? AND revision=? AND status<>'sold' AND active_order_id IS NULL AND ${guard}`).bind(action.priceKopecks,now,listing.id,this.owner,action.revision,...args),this.db.prepare('INSERT INTO market_price_history(listing_id,revision,price_kopecks,at) SELECT id,revision,price_kopecks,updated_at FROM market_listings WHERE id=? AND seller_id=? AND revision=? AND updated_at=? ON CONFLICT(listing_id,revision) DO NOTHING').bind(listing.id,this.owner,action.revision+1,now)]);if(!result[0].meta.changes)throw new AccountError('Цена уже изменилась. Загрузите свежую версию.',409,'revision_conflict');return {listing:await this.detail(listing.id)};
 }
}
