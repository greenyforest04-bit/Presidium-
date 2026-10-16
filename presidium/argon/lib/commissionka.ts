export const CATEGORIES=[['electronics','Электроника'],['cars','Автомобили'],['property','Недвижимость'],['home','Для дома'],['tools','Инструменты'],['fashion','Одежда'],['kids','Детские товары'],['other','Другое']] as const;
export type Category=typeof CATEGORIES[number][0];
export type Condition='used'|'new';
export type SellerType='private'|'manufacturer'|'company';
export type Verification='unverified'|'pending'|'verified';
export type Seller={id:string;name:string;type:SellerType;identity:Verification;rightToSell:Verification;ownership:Verification;manufacturer:Verification;matrix?:{userId:string;baseUrl:string}};
export type ListingImage={id:string;url:string;thumbnail:string;credit?:{author:string;license:string;url:string;source:string}};
export type PriceSnapshot={priceKopecks:number;at:number;revision:number};
export type Listing={id:string;title:string;description:string;category:Category;condition:Condition;priceKopecks:number;city:string;specs:Record<string,string>;images:ListingImage[];seller:Seller;status:'active'|'pending'|'sold'|'removed';conditionVerified:boolean;revision:number;createdAt:number;updatedAt:number;priceHistory:PriceSnapshot[];source:'server'|'fixture'};
export type MarketSort='relevant'|'newest'|'price-asc'|'price-desc';
export type MarketFilters={query:string;category:Category|'';condition:Condition|'';city:string;minPrice:string;maxPrice:string;verified:boolean;sellerType:SellerType|'';sort:MarketSort};
export const emptyFilters:MarketFilters={query:'',category:'',condition:'',city:'',minPrice:'',maxPrice:'',verified:false,sellerType:'',sort:'relevant'};
export type VatPolicy='unknown'|'none'|'included_22';
export type FeeBreakdown={priceKopecks:number;commissionBps:200|500;commissionKopecks:number;sellerBeforeExtrasKopecks:number;vatPolicy:VatPolicy;vatKopecks:number|null;platformNetKopecks:number|null;paymentOperatorKopecks:number|null;deliveryKopecks:number|null;payableKopecks:number|null};
export type DealCharge={kind:'payment'|'delivery'|'insurance'|'service';amountKopecks:number;payer:'buyer'|'seller';providerId:string;pricingVersion:string};
export type ConfirmedFeeBreakdown=FeeBreakdown&{vatPolicy:'none'|'included_22';vatKopecks:number;platformNetKopecks:number;paymentOperatorKopecks:number;deliveryKopecks:number;payableKopecks:number;charges:DealCharge[]};
export type OrderStatus='confirmed'|'paid_sandbox'|'paid'|'shipped'|'completed'|'cancelled'|'disputed';
export type Deal={id:string;listingId:string;buyerId:string;sellerId:string;lockedRevision:number;lockedPriceKopecks:number;fees:ConfirmedFeeBreakdown;confirmedAt:number;status:OrderStatus;paymentMode?:'sandbox'|'production';idempotencyKey?:string};
export type ServerOrderTerms={
 vatPolicy:VatPolicy;
 deliveryKopecks:number|null;
 paymentOperatorKopecks:number|null;
};
export type OrderFinancials={
 priceKopecks:number;
 commissionBps:200|500;
 commissionKopecks:number;
 vatPolicy:'none'|'included_22';
 vatKopecks:number;
 platformNetKopecks:number;
 deliveryKopecks:number;
 paymentOperatorKopecks:number;
 buyerTotalKopecks:number;
 sellerNetKopecks:number;
};
export type MarketDispute={
 id:string;
 orderId:string;
 initiatorId:string;
 reason:string;
 status:'open'|'resolved_refund'|'resolved_release'|'dismissed';
 resolutionNote:string;
 resolvedBy?:string|null;
 createdAt:number;
 updatedAt:number;
};
export type MarketOrder={
 id:string;
 listingId:string;
 listingTitle?:string;
 listingCity?:string;
 listingCategory?:Category;
 listingThumbnail?:string;
 buyerId:string;
 sellerId:string;
 sellerName?:string;
 sellerMatrix?:{userId:string;baseUrl:string};
 lockedRevision:number;
 lockedPriceKopecks:number;
 financials:OrderFinancials;
 status:OrderStatus;
 paymentMode:'sandbox'|'production';
 idempotencyKey:string;
 dispute?:MarketDispute;
 createdAt:number;
 updatedAt:number;
};
export type MarketplaceSubscription={tier:'free'|'plus'|'business'|'corporate';verified:boolean;periodEnd?:number};
export function marketplaceAccess(context?:MarketContext){return !!context?.sessionActive&&context.subscription.verified&&['plus','business'].includes(context.subscription.tier)&&(!context.subscription.periodEnd||context.subscription.periodEnd>Date.now())}
export type MarketContext={owner:string|null;sessionActive:boolean;subscription:MarketplaceSubscription;favoriteIds?:string[];capabilities:{catalog:boolean;publish:boolean;checkout:boolean;sandboxCheckout?:boolean;moderation?:boolean;verification:boolean;matrixBaseUrl:string|null};vatPolicy:VatPolicy;orderTerms?:ServerOrderTerms};
export type ListingPage={listings:Listing[];nextCursor:string|null;total:number};
export type ListingDraft={id:string;step:number;category:Category|'';condition:Condition;sellerType:SellerType;title:string;description:string;city:string;specs:{name:string;value:string}[];photoIds:string[];price:string;acceptedRules:boolean;conditionDeclaration:boolean;updatedAt:number};
export const MAX_PRICE_KOPECKS=99_999_999_999_999;
export function rublesToKopecks(value:string):number|null{const s=value.trim().replace(/[\s\u00a0\u202f]/g,'').replace(',','.');if(!/^\d{1,12}(\.\d{1,2})?$/.test(s))return null;const [whole,fraction='']=s.split('.'),n=BigInt(whole)*BigInt(100)+BigInt(fraction.padEnd(2,'0'));return n<=BigInt(MAX_PRICE_KOPECKS)?Number(n):null}
export function money(value:number){if(!Number.isSafeInteger(value)||value<0)throw Error('Некорректная денежная сумма');return new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',minimumFractionDigits:value%100?2:0,maximumFractionDigits:2}).format(value/100)}
function roundedRatio(value:number,numerator:number,denominator:number){return Number((BigInt(value)*BigInt(numerator)+BigInt(denominator/2))/BigInt(denominator))}
export function feeBreakdown(priceKopecks:number,condition:Condition,vatPolicy:VatPolicy='unknown',deliveryKopecks:number|null=null,paymentOperatorKopecks:number|null=null):FeeBreakdown{
 if(!Number.isSafeInteger(priceKopecks)||priceKopecks<1||priceKopecks>MAX_PRICE_KOPECKS)throw Error('Некорректная цена');
 if(deliveryKopecks!==null&&(!Number.isSafeInteger(deliveryKopecks)||deliveryKopecks<0||deliveryKopecks>MAX_PRICE_KOPECKS))throw Error('Некорректная стоимость доставки');
 if(paymentOperatorKopecks!==null&&(!Number.isSafeInteger(paymentOperatorKopecks)||paymentOperatorKopecks<0||paymentOperatorKopecks>MAX_PRICE_KOPECKS))throw Error('Некорректная комиссия платёжного оператора');
 const commissionBps=condition==='used'?200:500,commissionKopecks=roundedRatio(priceKopecks,commissionBps,10000);
 const vatKopecks=vatPolicy==='included_22'?roundedRatio(commissionKopecks,22,122):vatPolicy==='none'?0:null;
 const platformNetKopecks=vatKopecks===null?null:commissionKopecks-vatKopecks;
 const payableKopecks=vatPolicy!=='unknown'&&deliveryKopecks!==null&&paymentOperatorKopecks!==null?priceKopecks+deliveryKopecks+paymentOperatorKopecks:null;
 return {priceKopecks,commissionBps,commissionKopecks,sellerBeforeExtrasKopecks:priceKopecks-commissionKopecks,vatPolicy,vatKopecks,platformNetKopecks,paymentOperatorKopecks,deliveryKopecks,payableKopecks};
}
/**
 * Server-confirmed order calculation:
 * - Never substitutes 'none' for an 'unknown' VAT policy.
 * - Never substitutes 0 for null/unknown delivery or payment operator fees.
 * - Refuses to confirm a deal until all mandatory terms are explicitly defined on the server.
 */
export function confirmedOrderBreakdown(priceKopecks:number,condition:Condition,terms:ServerOrderTerms):OrderFinancials{
 if(terms.vatPolicy!=='none'&&terms.vatPolicy!=='included_22'){
  throw Error('Политика НДС не определена на сервере. Сделка не может быть подтверждена.');
 }
 if(terms.deliveryKopecks===null||!Number.isSafeInteger(terms.deliveryKopecks)||terms.deliveryKopecks<0){
  throw Error('Стоимость доставки не определена на сервере. Сделка не может быть подтверждена.');
 }
 if(terms.paymentOperatorKopecks===null||!Number.isSafeInteger(terms.paymentOperatorKopecks)||terms.paymentOperatorKopecks<0){
  throw Error('Комиссия платёжного оператора не определена на сервере. Сделка не может быть подтверждена.');
 }
 const base=feeBreakdown(priceKopecks,condition,terms.vatPolicy,terms.deliveryKopecks,terms.paymentOperatorKopecks);
 return {
  priceKopecks:base.priceKopecks,
  commissionBps:base.commissionBps,
  commissionKopecks:base.commissionKopecks,
  vatPolicy:terms.vatPolicy,
  vatKopecks:base.vatKopecks!,
  platformNetKopecks:base.platformNetKopecks!,
  deliveryKopecks:terms.deliveryKopecks,
  paymentOperatorKopecks:terms.paymentOperatorKopecks,
  buyerTotalKopecks:base.payableKopecks!,
  sellerNetKopecks:base.sellerBeforeExtrasKopecks,
 };
}
export const categoryName=(category:Category)=>CATEGORIES.find(([id])=>id===category)?.[1]||'Другое';
export const conditionName=(value:Condition)=>value==='used'?'Б/у':'Новое';
export const sellerTypeName=(value:SellerType)=>({private:'Частное лицо',manufacturer:'Производитель',company:'Компания'})[value];
export function filterListings(items:Listing[],filters:MarketFilters,includeUnavailable=false){
 const query=filters.query.trim().toLocaleLowerCase('ru-RU'),city=filters.city.trim().toLocaleLowerCase('ru-RU'),min=filters.minPrice?rublesToKopecks(filters.minPrice):null,max=filters.maxPrice?rublesToKopecks(filters.maxPrice):null;
 const results=items.filter(x=>(includeUnavailable||x.status==='active')&&(!query||`${x.title} ${categoryName(x.category)} ${x.city}`.toLocaleLowerCase('ru-RU').includes(query))&&(!filters.category||x.category===filters.category)&&(!filters.condition||x.condition===filters.condition)&&(!city||x.city.toLocaleLowerCase('ru-RU').includes(city))&&(min===null||x.priceKopecks>=min)&&(max===null||x.priceKopecks<=max)&&(!filters.verified||x.seller.identity==='verified')&&(!filters.sellerType||x.seller.type===filters.sellerType));
 return results.sort((a,b)=>filters.sort==='price-asc'?a.priceKopecks-b.priceKopecks||a.id.localeCompare(b.id):filters.sort==='price-desc'?b.priceKopecks-a.priceKopecks||a.id.localeCompare(b.id):b.createdAt-a.createdAt||a.id.localeCompare(b.id));
}
export function activeFilterCount(f:MarketFilters){return [f.category,f.condition,f.city,f.minPrice,f.maxPrice,f.verified,f.sellerType].filter(Boolean).length}
export function draftErrors(d:ListingDraft,step=d.step):Record<string,string>{
 const errors:Record<string,string>={};
 if(step===0||step===3){if(!d.category)errors.category='Выберите категорию.';if(d.condition==='used'&&!d.conditionDeclaration)errors.conditionDeclaration='Подтвердите, что товар действительно был в использовании.'}
 if(step===1||step===3){if(d.title.trim().length<3)errors.title='Название: минимум 3 символа.';if(d.title.trim().length>120)errors.title='Название: максимум 120 символов.';if(d.description.trim().length<20)errors.description='Расскажите о товаре: минимум 20 символов.';if(d.description.length>2500)errors.description='Описание: максимум 2 500 символов.';if(!d.city.trim())errors.city='Укажите город.';if(!d.photoIds.length)errors.photos='Добавьте хотя бы одну фотографию.';if(d.specs.some(x=>!x.name.trim()||!x.value.trim()))errors.specs='Заполните название и значение характеристики.';if(new Set(d.specs.map(x=>x.name.trim().toLowerCase())).size!==d.specs.length)errors.specs='Названия характеристик не должны повторяться.'}
 if(step===2||step===3){const price=rublesToKopecks(d.price);if(!price)errors.price='Введите цену от 0,01 ₽, не более 999 999 999 999,99 ₽.'}
 if(step===3&&!d.acceptedRules)errors.rules='Примите правила размещения.';
 return errors;
}
export function newListingDraft():ListingDraft{return {id:crypto.randomUUID(),step:0,category:'',condition:'used',sellerType:'private',title:'',description:'',city:'',specs:[],photoIds:[],price:'',acceptedRules:false,conditionDeclaration:false,updatedAt:Date.now()}}
export function hasListingDraft(d:ListingDraft){return !!(d.title||d.description||d.category||d.city||d.price||d.photoIds.length||d.specs.length||d.conditionDeclaration||d.condition==='new'||d.sellerType!=='private')}
