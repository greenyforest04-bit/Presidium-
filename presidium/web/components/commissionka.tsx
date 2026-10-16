'use client';
import {lazy,useEffect,useRef,useState} from 'react';
import {Search,SlidersHorizontal,Heart,ArrowLeft,ArrowUpRight,X,MapPin,ShieldCheck,ChevronDown,Plus,RefreshCw,PackageSearch,FilePenLine,ChevronRight,MessageCircle,ShoppingBag,AlertTriangle,Check,Truck,Ban} from 'lucide-react';
import {toast} from 'sonner';
import type {State} from '@/lib/domain';
import {CATEGORIES,emptyFilters,filterListings,activeFilterCount,rublesToKopecks,money,marketplaceAccess,conditionName,categoryName,sellerTypeName,type MarketContext,type MarketFilters,type Listing,type ListingPage,type MarketOrder,type Category,type Condition} from '@/lib/commissionka';
import {readMarketLocal,updateMarketLocal,defaultMarketLocal,type MarketLocal} from '@/lib/commissionka-local';
import {marketplaceApi,MarketplaceError} from '@/lib/commissionka-client';
import {CategoryNavigation,ListingCard,ListingGallery,MarketScreen,MarketplacePaywall,DealBreakdown,listingStatusLabel} from './commissionka-parts';
import {DeferredSurface} from './deferred-surface';
import {useMatrixConnection} from './matrix-connection';
import '../app/commissionka.css';
const ListingEditor=lazy(()=>import('./commissionka-editor').then(x=>({default:x.ListingEditor})));

const orderStatusLabel=(status:MarketOrder['status'])=>({
 confirmed:'Заказ подтверждён · ожидает оплаты',
 paid_sandbox:'Тестовая оплата подтверждена (деньги не списываются)',
 paid:'Оплачено',
 shipped:'Товар отправлен продавцом',
 completed:'Сделка завершена · товар получен',
 cancelled:'Заказ отменён',
 disputed:'Открыт спор · на рассмотрении',
})[status];

const disputeStatusLabel=(status:NonNullable<MarketOrder['dispute']>['status'])=>({
 open:'Открыт · ожидает решения арбитража',
 resolved_refund:'Решён в пользу покупателя (возврат)',
 resolved_release:'Решён в пользу продавца',
 dismissed:'Отклонён модератором',
})[status];

function MarketplaceFilters({value,onApply,onClose}:{value:MarketFilters;onApply:(value:MarketFilters)=>void;onClose:()=>void}){const [draft,setDraft]=useState({...value}),[error,setError]=useState('');function apply(){const min=draft.minPrice?rublesToKopecks(draft.minPrice):null,max=draft.maxPrice?rublesToKopecks(draft.maxPrice):null;if(draft.minPrice&&min===null||draft.maxPrice&&max===null){setError('Введите цены в рублях, до двух знаков после запятой.');return}if(min!==null&&max!==null&&min>max){setError('Минимальная цена не может быть выше максимальной.');return}onApply(draft);onClose()}return <MarketScreen title="Фильтры" className="market-filter-screen" onClose={onClose} footer={<><button className="market-secondary" onClick={()=>{setDraft({...emptyFilters,query:draft.query,sort:draft.sort});setError('')}}>Сбросить</button><button className="market-primary" onClick={apply}>Показать товары<ArrowUpRight size={18}/></button></>}><div className="market-filter-body"><label className="market-field">Категория<select value={draft.category} onChange={e=>setDraft({...draft,category:e.target.value as MarketFilters['category']})}><option value="">Все категории</option>{CATEGORIES.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><h3>Состояние</h3><div className="market-choice">{[['','Любое'],['used','Б/у'],['new','Новое']].map(([id,label])=><button key={id} aria-pressed={draft.condition===id} onClick={()=>setDraft({...draft,condition:id as MarketFilters['condition']})}>{label}</button>)}</div><h3>Цена, ₽</h3><div className="market-price-range"><label className="market-field"><span className="sr-only">Цена от</span><input aria-label="Цена от" inputMode="decimal" placeholder="От" value={draft.minPrice} maxLength={20} onChange={e=>setDraft({...draft,minPrice:e.target.value})}/></label><span>—</span><label className="market-field"><span className="sr-only">Цена до</span><input aria-label="Цена до" inputMode="decimal" placeholder="До" value={draft.maxPrice} maxLength={20} onChange={e=>setDraft({...draft,maxPrice:e.target.value})}/></label></div><label className="market-field">Город<input maxLength={80} placeholder="Любой город" value={draft.city} onChange={e=>setDraft({...draft,city:e.target.value})}/></label><label className="market-field">Продавец<select value={draft.sellerType} onChange={e=>setDraft({...draft,sellerType:e.target.value as MarketFilters['sellerType']})}><option value="">Любой</option>{(['private','manufacturer','company'] as const).map(type=><option key={type} value={type}>{sellerTypeName(type)}</option>)}</select></label><label className="market-check"><input type="checkbox" checked={draft.verified} onChange={e=>setDraft({...draft,verified:e.target.checked})}/><span>Личность продавца подтверждена<small>Только при наличии реальной проверки</small></span></label>{error&&<p role="alert" className="market-error">{error}</p>}</div></MarketScreen>}

function OrderDetailsScreen({order,context,onClose,onUpdated,onOpenListing,onContactSeller}:{order:MarketOrder;context?:MarketContext;onClose:()=>void;onUpdated:(order:MarketOrder)=>void;onOpenListing:(id:string)=>void;onContactSeller:(order:MarketOrder)=>void}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[disputeOpen,setDisputeOpen]=useState(false),[disputeReason,setDisputeReason]=useState(''),[modDecision,setModDecision]=useState<'refund_buyer'|'release_seller'|'dismiss'>('refund_buyer'),[modNote,setModNote]=useState('');
 const isBuyer=context?.owner===order.buyerId,isSeller=context?.owner===order.sellerId,isModerator=!!context?.capabilities.moderation;
 const payIdemKey=useRef(crypto.randomUUID());
 async function runAction(fn:()=>Promise<{order:MarketOrder}>){
  if(busy)return;setBusy(true);setError('');
  try{const res=await fn();onUpdated(res.order);setDisputeOpen(false)}
  catch(e){setError(e instanceof Error?e.message:'Не удалось выполнить операцию по заказу.')}
  finally{setBusy(false)}
 }
 return <MarketScreen title="Карточка заказа" description="Фиксированные условия сделки и статус выполнения." onClose={onClose} className="market-details">
  <div className="market-detail-body">
   {error&&<p className="market-error" role="alert">{error}</p>}
   <div className="market-fixture-notice" role="status">
    <span>{order.paymentMode==='sandbox'?'Тестовая оплата — деньги не списываются':'Заказ Комиссионки'} · Ревизия #{order.lockedRevision}</span>
    <b>{orderStatusLabel(order.status)}</b>
   </div>
   <h1>{order.listingTitle||'Товар по заказу'}</h1>
   <div className="market-detail-meta">
    <span>Заказ #{order.id.slice(0,8)}</span>
    {order.listingCity&&<span><MapPin size={15}/>{order.listingCity}</span>}
    <span>{isBuyer?'Вы покупатель':isSeller?'Вы продавец':'Просмотр'}</span>
   </div>
   <div className="market-detail-price"><strong>{money(order.lockedPriceKopecks)}</strong></div>
   <span className="market-price-label">Зафиксированная сервером цена на момент подтверждения заказа (не меняется при редактировании объявления)</span>
   <DealBreakdown price={order.lockedPriceKopecks} condition={order.financials.commissionBps===200?'used':'new'} vatPolicy={order.financials.vatPolicy} confirmedFinancials={order.financials}/>
   <div className="market-owner-actions">
    <button className="market-secondary" onClick={()=>{onClose();onOpenListing(order.listingId)}}>Открыть объявление<ArrowUpRight size={15}/></button>
    {order.sellerMatrix&&isBuyer&&<button className="market-secondary" disabled={busy} onClick={()=>onContactSeller(order)}><MessageCircle size={17}/>Написать продавцу в Matrix</button>}
    {isBuyer&&order.status==='confirmed'&&(context?.capabilities.sandboxCheckout?<button className="market-primary" disabled={busy} onClick={()=>void runAction(()=>marketplaceApi.act<{order:MarketOrder}>({action:'paySandbox',orderId:order.id,idempotencyKey:payIdemKey.current}))}><Check size={17}/>Подтвердить тестовую оплату (деньги не списываются)</button>:<p className="market-note">Реальный платёжный провайдер не подключён, тестовый контур оплаты отключён на сервере.</p>)}
    {isSeller&&(order.status==='paid_sandbox'||order.status==='paid')&&<button className="market-primary" disabled={busy} onClick={()=>void runAction(()=>marketplaceApi.act<{order:MarketOrder}>({action:'orderStatus',orderId:order.id,status:'shipped'}))}><Truck size={17}/>Отметить отправку товара</button>}
    {isBuyer&&order.status==='shipped'&&<button className="market-primary" disabled={busy} onClick={()=>void runAction(()=>marketplaceApi.act<{order:MarketOrder}>({action:'orderStatus',orderId:order.id,status:'completed'}))}><Check size={17}/>Подтвердить получение товара</button>}
    {(isBuyer||isSeller)&&(order.status==='confirmed'||order.status==='paid_sandbox')&&<button className="market-text-danger" disabled={busy} onClick={()=>void runAction(()=>marketplaceApi.act<{order:MarketOrder}>({action:'orderStatus',orderId:order.id,status:'cancelled'}))}><Ban size={16}/>Отменить заказ</button>}
    {(isBuyer||isSeller)&&['paid_sandbox','paid','shipped'].includes(order.status)&&!order.dispute&&<button className="market-secondary" disabled={busy} onClick={()=>setDisputeOpen(v=>!v)}><AlertTriangle size={16}/>Открыть спор</button>}
   </div>
   {disputeOpen&&<div className="market-exit-prompt">
    <h3>Открыть спор по заказу</h3>
    <p>Опишите причину (от 10 до 1000 символов). Заказ перейдёт на рассмотрение модератора.</p>
    <label className="market-field">Причина спора<textarea rows={3} maxLength={1000} value={disputeReason} onChange={e=>setDisputeReason(e.target.value)} placeholder="Укажите несоответствие описания, задержку или проблему с доставкой"/></label>
    <div>
     <button className="market-secondary" disabled={busy} onClick={()=>setDisputeOpen(false)}>Отмена</button>
     <button className="market-primary" disabled={busy||disputeReason.trim().length<10} onClick={()=>void runAction(()=>marketplaceApi.act<{order:MarketOrder}>({action:'dispute',orderId:order.id,reason:disputeReason.trim()}))}>Отправить спор</button>
    </div>
   </div>}
   {order.dispute&&<section className="market-specs" aria-label="Состояние спора">
    <h3>Спор по заказу</h3>
    <dl>
     <div><dt>Статус</dt><dd>{disputeStatusLabel(order.dispute.status)}</dd></div>
     <div><dt>Причина обращения</dt><dd>{order.dispute.reason}</dd></div>
     {order.dispute.resolutionNote&&<div><dt>Решение арбитража</dt><dd>{order.dispute.resolutionNote}</dd></div>}
    </dl>
    {isModerator&&order.dispute.status==='open'&&<div className="market-exit-prompt">
     <h3>Решение арбитража (доступ модератора)</h3>
     <label className="market-field">Решение<select value={modDecision} onChange={e=>setModDecision(e.target.value as typeof modDecision)}><option value="refund_buyer">Отменить сделку (возврат покупателю)</option><option value="release_seller">Завершить сделку в пользу продавца</option><option value="dismiss">Отклонить спор и завершить сделку</option></select></label>
     <label className="market-field">Комментарий арбитража<input maxLength={500} value={modNote} onChange={e=>setModNote(e.target.value)} placeholder="Обоснование решения (минимум 5 символов)"/></label>
     <button className="market-primary" disabled={busy||modNote.trim().length<5} onClick={()=>void runAction(()=>marketplaceApi.act<{order:MarketOrder}>({action:'resolveDispute',disputeId:order.dispute!.id,decision:modDecision,resolutionNote:modNote.trim()}))}>Применить решение</button>
    </div>}
   </section>}
  </div>
 </MarketScreen>;
}

function ListingDetails({listing,saved,context,loading,error,busy,onClose,onFavorite,onContact,onPurchase,onUpdate,onRefreshLatest,onUpgrade}:{listing:Listing;saved:boolean;context?:MarketContext;loading:boolean;error:string;busy:boolean;onClose:()=>void;onFavorite:()=>void;onContact:()=>void;onPurchase:()=>void;onUpdate:(listing:Listing)=>void;onRefreshLatest:()=>Promise<Listing|undefined>;onUpgrade:()=>void}){
 const paid=marketplaceAccess(context),[newPrice,setNewPrice]=useState(''),[priceEdit,setPriceEdit]=useState(false),[editOpen,setEditOpen]=useState(false),[editDraft,setEditDraft]=useState(()=>({title:listing.title,description:listing.description,category:listing.category as Category,condition:listing.condition as Condition,city:listing.city,specs:Object.entries(listing.specs).map(([name,value])=>({name,value}))})),[conflict,setConflict]=useState(false),[unlink,setUnlink]=useState(false),[localError,setLocalError]=useState(''),[updating,setUpdating]=useState(false),[withdraw,setWithdraw]=useState(false),[modNote,setModNote]=useState(''),[modCondVerified,setModCondVerified]=useState(true),[modRightVerified,setModRightVerified]=useState(true);
 const owner=listing.source==='server'&&context?.sessionActive&&context.owner===listing.seller.id,isModerator=!!context?.capabilities.moderation;
 async function updatePrice(){
  const price=rublesToKopecks(newPrice);if(!price){setLocalError('Введите положительную цену в рублях.');return}
  setUpdating(true);setConflict(false);
  try{const r=await marketplaceApi.act<{listing:Listing}>({action:'price',listingId:listing.id,revision:listing.revision,priceKopecks:price});onUpdate(r.listing);setPriceEdit(false);setLocalError('')}
  catch(e){setLocalError(e instanceof Error?e.message:'Цена не изменена.');if(e instanceof MarketplaceError&&(e.code==='revision_conflict'||e.status===409))setConflict(true)}
  finally{setUpdating(false)}
 }
 async function saveEditedListing(){
  if(editDraft.title.trim().length<3||editDraft.description.trim().length<20||!editDraft.city.trim()){setLocalError('Проверьте название (от 3 символов), описание (от 20 символов) и город.');return}
  setUpdating(true);setConflict(false);
  try{
   const r=await marketplaceApi.act<{listing:Listing}>({action:'update',listingId:listing.id,revision:listing.revision,title:editDraft.title.trim(),description:editDraft.description.trim(),category:editDraft.category,condition:editDraft.condition,city:editDraft.city.trim(),specs:editDraft.specs.filter(s=>s.name.trim()&&s.value.trim()).map(s=>({name:s.name.trim(),value:s.value.trim()}))});
   onUpdate(r.listing);setEditOpen(false);setLocalError('');toast.success('Изменения сохранены и отправлены на проверку модератору.');
  }catch(e){
   setLocalError(e instanceof Error?e.message:'Изменения не сохранены.');
   if(e instanceof MarketplaceError&&(e.code==='revision_conflict'||e.status===409))setConflict(true);
  }finally{setUpdating(false)}
 }
 async function remove(){
  setUpdating(true);setConflict(false);
  try{await marketplaceApi.act({action:'withdraw',listingId:listing.id,revision:listing.revision});onUpdate({...listing,status:'removed',revision:listing.revision+1});setWithdraw(false)}
  catch(e){setLocalError(e instanceof Error?e.message:'Объявление не снято.');if(e instanceof MarketplaceError&&(e.code==='revision_conflict'||e.status===409))setConflict(true)}
  finally{setUpdating(false)}
 }
 async function moderate(decision:'approve'|'reject'){
  setUpdating(true);setConflict(false);
  try{const r=await marketplaceApi.act<{listing:Listing}>({action:'moderateListing',listingId:listing.id,revision:listing.revision,decision,conditionVerified:decision==='approve'&&modCondVerified,rightToSellVerified:decision==='approve'&&modRightVerified,note:modNote.trim()||undefined});onUpdate(r.listing);setLocalError('');toast.success(decision==='approve'?'Объявление одобрено и опубликовано в каталоге.':'Объявление отклонено модератором.')}
  catch(e){setLocalError(e instanceof Error?e.message:'Не удалось выполнить модерацию.');if(e instanceof MarketplaceError&&(e.code==='revision_conflict'||e.status===409))setConflict(true)}
  finally{setUpdating(false)}
 }
 const unavailable=listing.status!=='active'||listing.source==='fixture';
 return <MarketScreen title={listing.source==='fixture'?'Пример объявления':'Объявление'} description="Фотографии, цена, характеристики и условия продажи." onClose={onClose} className="market-details" footer={<><div className="market-detail-buttons"><button className="market-secondary" disabled={busy||loading||unavailable||!listing.seller.matrix} onClick={onContact}><MessageCircle size={19}/>Написать продавцу</button><button className="market-primary" disabled={busy||loading||unavailable||owner||paid&&!context?.capabilities.checkout} onClick={onPurchase}>{context?.capabilities.sandboxCheckout?'Оформить заказ (тестовый контур)':'Оформить покупку'}</button></div><small>{listing.source==='fixture'?'Учебное объявление. Связь и сделки недоступны.':listing.status==='pending'?'Статус: ожидает проверки модератором. Самостоятельная публикация продавцом запрещена.':listing.status==='sold'?'Статус: товар продан.':listing.status==='removed'?'Статус: объявление снято с публикации.':owner?'Это ваше объявление.':context?.capabilities.sandboxCheckout?'Тестовая оплата — деньги не списываются. Условия и цена фиксируются сервером атомарно.':'Оформление заказа и реальный платёжный провайдер пока не подключены на сервере.'}</small></>}>
 <ListingGallery images={listing.images} title={listing.title} category={listing.category}/><div className="market-detail-body">{loading&&<p className="market-note" role="status">Обновляем данные объявления…</p>}{(error||localError)&&<div className="market-error" role="alert"><p>{error||localError}</p>{conflict&&<button className="market-secondary" disabled={updating} onClick={async()=>{const fresh=await onRefreshLatest();if(fresh){setConflict(false);setLocalError('');toast.info('Актуальная ревизия загружена. Ваши введённые данные сохранены в форме.')}}}><RefreshCw size={15}/>Получить актуальное состояние (сохранить введённый текст)</button>}</div>}<div className="market-detail-price"><strong>{money(listing.priceKopecks)}</strong><button className="market-icon" aria-label={saved?'Убрать товар из избранного':'Добавить товар в избранное'} aria-pressed={saved} disabled={busy} onClick={onFavorite}><Heart fill={saved?'currentColor':'none'} size={23}/></button></div><span className="market-price-label">Фиксированная цена предложения · Статус: {listingStatusLabel(listing.status,listing.source)} · Ревизия #{listing.revision}</span>{listing.source==='server'&&<button className="market-listing-link" onClick={async()=>{try{await navigator.clipboard.writeText(new URL('/?listing='+listing.id,window.location.origin).href);toast.success('Ссылка на объявление скопирована.')}catch{setLocalError('Не удалось скопировать ссылку.')}}}>Ссылка на объявление<ArrowUpRight size={14}/></button>}<h1>{listing.title}</h1><div className="market-detail-meta"><span>{conditionName(listing.condition)}</span><span><MapPin size={15}/>{listing.city}</span><span>{categoryName(listing.category)}</span></div><p className="market-description">{listing.description}</p>{Object.keys(listing.specs).length>0&&<section className="market-specs"><h3>Характеристики</h3><dl>{Object.entries(listing.specs).map(([key,value])=><div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl></section>}<section className="market-seller"><div className="market-seller-avatar">{listing.seller.name.slice(0,1)}</div><div><h3>{listing.seller.name}</h3><p>{sellerTypeName(listing.seller.type)} · указано продавцом</p></div><div className="market-verification"><span>{listing.seller.identity==='verified'?<ShieldCheck size={16}/>:null}{listing.seller.identity==='verified'?'Личность подтверждена':listing.seller.identity==='pending'?'Проверка личности не завершена':'Личность не проверена'}</span><span>{listing.seller.rightToSell==='verified'?'Право продажи проверено':listing.seller.rightToSell==='pending'?'Право продажи проверяется':'Право продажи не проверено'}</span>{(['cars','property'].includes(listing.category)||listing.seller.ownership==='verified')&&<span>{listing.seller.ownership==='verified'?'Собственник подтверждён':listing.seller.ownership==='pending'?'Проверка собственника не завершена':'Собственник не подтверждён'}</span>}{listing.seller.type==='manufacturer'&&<span>{listing.seller.manufacturer==='verified'?'Производитель подтверждён':'Производитель не подтверждён'}</span>}</div></section><DealBreakdown price={listing.priceKopecks} condition={listing.condition} vatPolicy={context?.vatPolicy} orderTerms={context?.orderTerms} preliminary={!listing.conditionVerified}/><details className="market-price-history"><summary>История цены<ChevronDown size={17}/></summary>{listing.priceHistory.length?listing.priceHistory.map(p=><div key={p.revision}><time>{new Date(p.at).toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric'})} · рев. #{p.revision}</time><strong>{money(p.priceKopecks)}</strong></div>):<p>{listing.source==='fixture'?'История не ведётся для учебных объявлений.':'История цены пока недоступна.'}</p>}</details>
 {owner&&listing.status!=='removed'&&listing.status!=='sold'&&<section className="market-owner-actions">
  <button className="market-secondary" onClick={()=>{if(!paid){onUpgrade();return}setPriceEdit(!priceEdit);if(!newPrice)setNewPrice(String(listing.priceKopecks/100))}}>Изменить цену</button>
  <button className="market-secondary" onClick={()=>{if(!paid){onUpgrade();return}setEditOpen(!editOpen)}}>Редактировать объявление</button>
  <button className="market-text-danger" onClick={()=>setWithdraw(true)}>Снять объявление</button>
  {listing.seller.matrix&&<button className="market-secondary" onClick={()=>setUnlink(true)}>Отвязать контакт Matrix</button>}
  {unlink&&<div className="market-exit-prompt"><p>Контакт Matrix исчезнет из ваших объявлений. Переписка и её ключи сохранятся.</p><button className="market-secondary" onClick={()=>setUnlink(false)}>Оставить</button><button className="market-text-danger" disabled={updating} onClick={async()=>{setUpdating(true);try{await marketplaceApi.act({action:'unlinkMatrix'});onUpdate({...listing,seller:{...listing.seller,matrix:undefined}});setUnlink(false)}catch(e){setLocalError(e instanceof Error?e.message:'Контакт не отвязан.')}finally{setUpdating(false)}}}>Отвязать</button></div>}
  {priceEdit&&<label className="market-field">Новая цена, ₽<input inputMode="decimal" value={newPrice} onChange={e=>setNewPrice(e.target.value)}/><button className="market-primary" disabled={updating} onClick={()=>void updatePrice()}>Сохранить в истории (рев. #{listing.revision})</button></label>}
  {editOpen&&<div className="market-exit-prompt">
   <h3>Редактирование (ревизия #{listing.revision})</h3>
   <p>После сохранения объявление перейдёт в статус «Ожидает проверки» модератором. При конфликте ревизий ваши введённые данные сохранятся.</p>
   <label className="market-field">Категория<select disabled={updating} value={editDraft.category} onChange={e=>setEditDraft({...editDraft,category:e.target.value as Category})}>{CATEGORIES.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
   <label className="market-field">Состояние<select disabled={updating} value={editDraft.condition} onChange={e=>setEditDraft({...editDraft,condition:e.target.value as Condition})}><option value="used">Б/у (комиссия 2%)</option><option value="new">Новое (комиссия 5%)</option></select></label>
   <label className="market-field">Название<input disabled={updating} maxLength={120} value={editDraft.title} onChange={e=>setEditDraft({...editDraft,title:e.target.value})}/></label>
   <label className="market-field">Город<input disabled={updating} maxLength={80} value={editDraft.city} onChange={e=>setEditDraft({...editDraft,city:e.target.value})}/></label>
   <label className="market-field">Описание<textarea disabled={updating} rows={4} maxLength={2500} value={editDraft.description} onChange={e=>setEditDraft({...editDraft,description:e.target.value})}/></label>
   <div><button className="market-secondary" disabled={updating} onClick={()=>setEditOpen(false)}>Отмена</button><button className="market-primary" disabled={updating} onClick={()=>void saveEditedListing()}>Сохранить изменения</button></div>
  </div>}
  {withdraw&&<div className="market-exit-prompt"><p>Снять объявление с продажи?</p><button className="market-secondary" disabled={updating} onClick={()=>setWithdraw(false)}>Оставить</button><button className="market-text-danger" disabled={updating} onClick={()=>void remove()}>Снять</button></div>}
 </section>}
 {isModerator&&listing.source==='server'&&listing.status!=='sold'&&<section className="market-owner-actions" aria-label="Панель модератора">
  <h3>Модерация объявления (доступ модератора)</h3>
  <label className="market-check"><input type="checkbox" checked={modCondVerified} onChange={e=>setModCondVerified(e.target.checked)}/><span>Состояние товара проверено модератором</span></label>
  <label className="market-check"><input type="checkbox" checked={modRightVerified} onChange={e=>setModRightVerified(e.target.checked)}/><span>Право распоряжения проверено модератором</span></label>
  <label className="market-field">Примечание модератора<input maxLength={500} value={modNote} onChange={e=>setModNote(e.target.value)} placeholder="Комментарий к проверке"/></label>
  <div>
   <button className="market-primary" disabled={updating} onClick={()=>void moderate('approve')}><Check size={16}/>Одобрить и опубликовать</button>
   <button className="market-text-danger" disabled={updating} onClick={()=>void moderate('reject')}><Ban size={16}/>Отклонить и снять</button>
  </div>
 </section>}
 {listing.images.some(x=>x.credit)&&<details className="market-photo-credits"><summary>Источники фото примера<ChevronDown size={17}/></summary>{listing.images.map(image=>image.credit&&<p key={image.id}><a href={image.credit.source} target="_blank" rel="noreferrer">{image.credit.author}</a> · <a href={image.credit.url} target="_blank" rel="noreferrer">{image.credit.license}</a>. Размер изменён, фото сохранено в WebP.</p>)}</details>}<p className="market-note">{context?.capabilities.sandboxCheckout?'Включён тестовый контур заказов (песочница — деньги не списываются). Цена и ревизия фиксируются сервером атомарно при подтверждении заказа.':'Цена будущего заказа и расходы фиксируются сервером атомарно. Реальный платёжный провайдер пока не подключён.'}</p></div>
 </MarketScreen>;
}

export function CommissionkaHome({data,createSignal=0,onCreateHandled,onAccount,refreshSignal=0,initialListingId,onListingHandled}:{initialListingId?:string;onListingHandled:()=>void;data:State;createSignal?:number;onCreateHandled:()=>void;onAccount:()=>void;refreshSignal?:number}){
 const network=useMatrixConnection(),[context,setContext]=useState<MarketContext>(),[contextError,setContextError]=useState(''),[scope,setScope]=useState('device'),[local,setLocal]=useState<MarketLocal>(defaultMarketLocal),[initialized,setInitialized]=useState(false),[filters,setFilters]=useState<MarketFilters>(emptyFilters),[source,setSource]=useState<'server'|'fixture'>('server'),[fixture,setFixture]=useState<Listing[]>(),[mode,setMode]=useState<'catalog'|'favorites'|'own'|'orders'>('catalog'),[orderRoleFilter,setOrderRoleFilter]=useState<'all'|'purchases'|'sales'>('all'),[orders,setOrders]=useState<MarketOrder[]>([]),[activeOrder,setActiveOrder]=useState<MarketOrder>(),[page,setPage]=useState<ListingPage>({listings:[],nextCursor:null,total:0}),[loading,setLoading]=useState(true),[more,setMore]=useState(false),[catalogError,setCatalogError]=useState(''),[cachedFavorites,setCachedFavorites]=useState(false),[retry,setRetry]=useState(0),[filterOpen,setFilterOpen]=useState(false),[paywall,setPaywall]=useState(false),[editor,setEditor]=useState(false),[active,setActive]=useState<Listing>(),[detailLoading,setDetailLoading]=useState(false),[detailError,setDetailError]=useState(''),[missingListing,setMissingListing]=useState(''),[pendingFavorites,setPendingFavorites]=useState<Set<string>>(new Set()),[contactBusy,setContactBusy]=useState(false);
 const root=useRef<HTMLElement>(null),activeRef=useRef(active),favoriteLocks=useRef(new Set<string>()),checkoutKeysRef=useRef(new Map<string,string>()),localRef=useRef(local),generation=useRef(0),detailRequest=useRef<AbortController|null>(null),moreRequest=useRef<AbortController|null>(null),contactRequest=useRef<AbortController|null>(null),alive=useRef(true),initializedRef=useRef(false);localRef.current=local;activeRef.current=active;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;detailRequest.current?.abort();moreRequest.current?.abort();contactRequest.current?.abort()}},[]);
 useEffect(()=>{
  const controller=new AbortController();
  async function initialize(){
   try{
    const value=await marketplaceApi.context(controller.signal);
    if(controller.signal.aborted)return;
    setContext(value);setContextError('');
    const nextScope=value.owner||'device';
    if(!initializedRef.current||scope!==nextScope){
     let prefs:MarketLocal=defaultMarketLocal;
     try{prefs=await readMarketLocal(nextScope)}catch{setContextError('Данные устройства не удалось открыть. Каталог доступен; сохранение черновика нужно проверить.')}
     if(controller.signal.aborted)return;
     const favorites=[...prefs.favorites.filter(id=>id.startsWith('fixture-')),...(value.sessionActive?value.favoriteIds||[]:prefs.favorites.filter(id=>!id.startsWith('fixture-')))];
     const merged={...prefs,favorites};
     try{await updateMarketLocal(nextScope,v=>({...v,favorites}))}catch{setContextError('Локальные настройки не сохранены. Проверьте место на устройстве.')}
     if(controller.signal.aborted)return;
     setScope(nextScope);setFilters(prefs.filters);setSource(prefs.source);setLocal(merged);
     initializedRef.current=true;setInitialized(true);
     requestAnimationFrame(()=>{const scroller=root.current?.closest('.feed-page');if(scroller)scroller.scrollTop=prefs.scrollTop});
    }else if(value.sessionActive){
     const favorites=[...localRef.current.favorites.filter(id=>id.startsWith('fixture-')),...(value.favoriteIds||[])];
     try{const next=await updateMarketLocal(nextScope,v=>({...v,favorites}));if(!controller.signal.aborted)setLocal(next)}catch{if(!controller.signal.aborted)setLocal(v=>({...v,favorites}))}
    }
   }catch(error){
    if(controller.signal.aborted)return;
    setContext(undefined);setContextError(error instanceof Error?error.message:'Не удалось проверить профиль.');
    if(!initializedRef.current){
     try{const prefs=await readMarketLocal('device');if(controller.signal.aborted)return;setLocal(prefs);setFilters(prefs.filters);setSource(prefs.source)}catch{}
     initializedRef.current=true;setInitialized(true);
    }
   }
  }
  void initialize();return()=>controller.abort();
 },[refreshSignal,retry]);
 useEffect(()=>{if(!initialized)return;const timer=setTimeout(()=>void updateMarketLocal(scope,v=>({...v,filters,source})).catch(()=>toast.error('Не удалось сохранить настройки Комиссионки.')),400);return()=>clearTimeout(timer)},[scope,filters,source,initialized]);
 useEffect(()=>{if(!initialized)return;const el=root.current?.closest('.feed-page');if(!el)return;let timer:ReturnType<typeof setTimeout>|undefined;const save=()=>{clearTimeout(timer);timer=setTimeout(()=>void updateMarketLocal(scope,v=>({...v,scrollTop:el.scrollTop})).catch(()=>{}),500)};el.addEventListener('scroll',save,{passive:true});return()=>{el.removeEventListener('scroll',save);clearTimeout(timer);void updateMarketLocal(scope,v=>({...v,scrollTop:el.scrollTop})).catch(()=>{})}},[scope,initialized]);
 useEffect(()=>{if(source!=='fixture'||fixture)return;let active=true;void import('@/lib/commissionka-fixtures').then(m=>{if(active)setFixture(m.marketFixtures)}).catch(()=>{if(active){setCatalogError('Не удалось открыть пример каталога.');setLoading(false)}});return()=>{active=false}},[source,fixture,retry]);
 useEffect(()=>{if(!initialListingId||!initialized)return;const c=new AbortController();setSource('server');void marketplaceApi.detail(initialListingId,c.signal).then(item=>{if(!c.signal.aborted){setActive(item);setDetailError('')}}).catch(e=>{if(!c.signal.aborted)setMissingListing(e instanceof Error?e.message:'Объявление недоступно.')}).finally(()=>{if(!c.signal.aborted)onListingHandled()});return()=>c.abort()},[initialListingId,initialized]);
 function requestCreate(){if(marketplaceAccess(context)){setEditor(true)}else setPaywall(true)}
 useEffect(()=>{if(createSignal&&initialized){requestCreate();onCreateHandled()}},[createSignal,initialized]);
 useEffect(()=>{
  if(!initialized)return;
  const c=new AbortController(),epoch=++generation.current;moreRequest.current?.abort();setLoading(true);setCatalogError('');setCachedFavorites(false);
  const timer=setTimeout(()=>{
   if(mode==='orders'){
    if(!context?.sessionActive){setOrders([]);setLoading(false);return}
    void marketplaceApi.orders(c.signal).then(list=>{if(!c.signal.aborted&&epoch===generation.current)setOrders(list)}).catch(error=>{if(!c.signal.aborted&&epoch===generation.current){setOrders([]);setCatalogError(error instanceof Error?error.message:'Заказы не загрузились.')}}).finally(()=>{if(!c.signal.aborted&&epoch===generation.current)setLoading(false)});
    return;
   }
   if(source==='fixture'){if(!fixture)return;const candidates=mode==='favorites'?fixture.filter(x=>localRef.current.favorites.includes(x.id)):mode==='own'?[]:fixture,shown=filterListings(candidates,filters);setPage({listings:shown,nextCursor:null,total:shown.length});setLoading(false);return}
   const task=mode==='favorites'?(context?.sessionActive?marketplaceApi.favorites(c.signal):Promise.resolve(localRef.current.savedListings.filter(x=>localRef.current.favorites.includes(x.id)))).then(items=>{if(!context?.sessionActive)setCachedFavorites(true);const shown=filterListings(items,filters,true);return {listings:shown,nextCursor:null,total:shown.length}}):mode==='own'?marketplaceApi.own(c.signal).then(items=>({listings:filterListings(items,filters,true),nextCursor:null,total:filterListings(items,filters,true).length})):marketplaceApi.catalog(filters,undefined,c.signal);
   void task.then(value=>{if(!c.signal.aborted&&epoch===generation.current)setPage(value)}).catch(error=>{if(!c.signal.aborted&&epoch===generation.current){setPage({listings:[],nextCursor:null,total:0});setCatalogError(error instanceof Error?error.message:'Каталог не загрузился.')}}).finally(()=>{if(!c.signal.aborted&&epoch===generation.current)setLoading(false)});
  },filters.query?240:0);
  return()=>{clearTimeout(timer);c.abort()};
 },[filters,mode,source,fixture,retry,initialized,context?.sessionActive,scope]);
 async function loadMore(){if(more||!page.nextCursor)return;const epoch=generation.current,c=new AbortController();moreRequest.current?.abort();moreRequest.current=c;setMore(true);try{const value=await marketplaceApi.catalog(filters,page.nextCursor,c.signal);if(!c.signal.aborted&&epoch===generation.current)setPage(previous=>({...value,listings:[...new Map([...previous.listings,...value.listings].map(x=>[x.id,x])).values()]}))}catch(error){if(!c.signal.aborted)setCatalogError(error instanceof Error?error.message:'Не удалось загрузить следующую страницу.')}finally{if(alive.current)setMore(false)}}
 async function favorite(listing:Listing){if(favoriteLocks.current.has(listing.id))return;favoriteLocks.current.add(listing.id);setPendingFavorites(new Set(favoriteLocks.current));const saved=!localRef.current.favorites.includes(listing.id);if(saved&&localRef.current.favorites.length>=200){favoriteLocks.current.delete(listing.id);setPendingFavorites(new Set(favoriteLocks.current));toast.error('В избранном уже 200 товаров. Уберите те, которые больше не нужны.');return}try{if(listing.source==='server'&&context?.sessionActive)await marketplaceApi.favorite(listing.id,saved);const value=await updateMarketLocal(scope,v=>({...v,favorites:saved?[...new Set([...v.favorites,listing.id])]:v.favorites.filter(x=>x!==listing.id),savedListings:saved?[...v.savedListings.filter(x=>x.id!==listing.id),listing].slice(-200):v.savedListings.filter(x=>x.id!==listing.id)}));if(alive.current){setLocal(value);if(mode==='favorites'&&!saved)setPage(v=>({...v,listings:v.listings.filter(x=>x.id!==listing.id),total:Math.max(0,v.total-1)}))}}catch(e){toast.error(e instanceof Error?e.message:'Не удалось сохранить товар.')}finally{favoriteLocks.current.delete(listing.id);if(alive.current)setPendingFavorites(new Set(favoriteLocks.current))}}
 function openListing(listing:Listing){contactRequest.current?.abort();detailRequest.current?.abort();setActive(listing);setDetailError('');if(listing.source==='fixture'){setDetailLoading(false);return}const c=new AbortController();detailRequest.current=c;setDetailLoading(true);void marketplaceApi.detail(listing.id,c.signal).then(item=>{if(!c.signal.aborted)setActive(item)}).catch(error=>{if(!c.signal.aborted){setDetailError(error instanceof Error?error.message:'Объявление недоступно.');if(error instanceof MarketplaceError&&error.code==='listing_missing')setActive(x=>x?{...x,status:'removed'}:x)}}).finally(()=>{if(!c.signal.aborted)setDetailLoading(false)})}
 async function refreshActiveListing():Promise<Listing|undefined>{
  if(!active||active.source==='fixture')return undefined;
  setDetailLoading(true);
  try{const fresh=await marketplaceApi.detail(active.id);if(alive.current){setActive(fresh);setDetailError('');setPage(p=>({...p,listings:p.listings.map(x=>x.id===fresh.id?fresh:x)}))}return fresh}
  catch(error){if(alive.current)setDetailError(error instanceof Error?error.message:'Не удалось обновить объявление.');return undefined}
  finally{if(alive.current)setDetailLoading(false)}
 }
 function closeListing(){contactRequest.current?.abort();detailRequest.current?.abort();setActive(undefined);setDetailError('');setDetailLoading(false)}
 async function contact(){const listing=active;if(!listing?.seller.matrix||listing.source==='fixture')return;const c=new AbortController();contactRequest.current?.abort();contactRequest.current=c;setContactBusy(true);setDetailError('');try{const opened=await network.openSeller(listing.seller.matrix,{id:listing.id,title:listing.title,priceKopecks:listing.priceKopecks,image:listing.images[0]?.thumbnail},()=>{if(alive.current)openListing(listing)},c.signal);if(c.signal.aborted||!alive.current)return;if(opened)closeListing();else setDetailError('Подключите Matrix в серверном профиле, чтобы открыть переписку.')}catch(error){if(!c.signal.aborted&&alive.current)setDetailError(error instanceof Error?error.message:'Продавец сейчас недоступен.')}finally{if(alive.current)setContactBusy(false)}}
 async function contactFromOrder(order:MarketOrder){
  if(!order.sellerMatrix)return;
  const c=new AbortController();contactRequest.current?.abort();contactRequest.current=c;setContactBusy(true);
  try{
   const opened=await network.openSeller(order.sellerMatrix,{id:order.listingId,title:order.listingTitle||'Заказ',priceKopecks:order.lockedPriceKopecks,image:order.listingThumbnail},()=>{if(alive.current)setActiveOrder(order)},c.signal);
   if(opened&&alive.current)setActiveOrder(undefined);
   else toast.error('Подключите Matrix в серверном профиле, чтобы открыть переписку.');
  }catch(e){toast.error(e instanceof Error?e.message:'Продавец сейчас недоступен.')}
  finally{if(alive.current)setContactBusy(false)}
 }
 async function purchase(){
  if(!active)return;if(!marketplaceAccess(context)){setPaywall(true);return}
  const keyScope=`${active.id}:${active.revision}`;
  let idemKey=checkoutKeysRef.current.get(keyScope);
  if(!idemKey){idemKey=crypto.randomUUID();checkoutKeysRef.current.set(keyScope,idemKey)}
  try{
   const res=await marketplaceApi.act<{order:MarketOrder;idempotent?:boolean}>({action:'checkout',listingId:active.id,revision:active.revision,idempotencyKey:idemKey});
   setActive(v=>v?{...v,status:'sold',revision:v.revision+1}:v);
   setOrders(prev=>[res.order,...prev.filter(o=>o.id!==res.order.id)]);
   closeListing();
   setActiveOrder(res.order);
   setMode('orders');
   toast.success('Заказ оформлен по зафиксированной серверной цене.');
  }catch(error){
   setDetailError(error instanceof Error?error.message:'Заказ не создан.');
   if(error instanceof MarketplaceError&&(error.code==='revision_conflict'||error.code==='listing_unavailable'))void refreshActiveListing();
  }
 }
 const count=activeFilterCount(filters),isFiltered=count>0||!!filters.query,title=mode==='favorites'?'Избранное':mode==='own'?'Мои объявления':mode==='orders'?'Мои покупки и продажи':'Предложения';
 const filteredOrders=orders.filter(o=>orderRoleFilter==='purchases'?o.buyerId===context?.owner:orderRoleFilter==='sales'?o.sellerId===context?.owner:true);
 return <section ref={root} className="commissionka" aria-label="Комиссионка"><div className="market-toolbar"><label className="market-search"><Search size={19}/><input placeholder="Что ищем?" aria-label="Поиск по объявлениям" maxLength={120} value={filters.query} onChange={e=>setFilters(f=>({...f,query:e.target.value}))}/>{filters.query&&<button className="market-icon" aria-label="Очистить поиск товара" onClick={()=>setFilters(f=>({...f,query:''}))}><X size={17}/></button>}</label><button className="market-toolbar-button" aria-label="Фильтры товаров" aria-expanded={filterOpen} onClick={()=>setFilterOpen(true)}><SlidersHorizontal size={21}/>{count>0&&<i>{count}</i>}</button><button className="market-toolbar-button" aria-label="Избранные товары" aria-pressed={mode==='favorites'} onClick={()=>setMode(mode==='favorites'?'catalog':'favorites')}><Heart size={22} fill={mode==='favorites'?'currentColor':'none'}/></button><button className="market-toolbar-button" aria-label="Мои покупки и продажи" aria-pressed={mode==='orders'} onClick={()=>{if(source==='fixture')setSource('server');setMode(mode==='orders'?'catalog':'orders')}}><ShoppingBag size={21}/></button></div><CategoryNavigation value={filters.category} onChange={category=>setFilters(f=>({...f,category}))}/>
 {source==='fixture'&&<div className="market-fixture-notice"><span>Пример каталога · учебные объявления</span><button onClick={()=>{setSource('server');setMode('catalog')}}>К каталогу<ArrowUpRight size={14}/></button></div>}
 <div className="market-catalog-heading"><div>{mode!=='catalog'&&<button className="market-icon" aria-label="Вернуться к каталогу" onClick={()=>setMode('catalog')}><ArrowLeft size={19}/></button>}<h2>{title}</h2>{!loading&&<span>{mode==='orders'?filteredOrders.length:page.total}</span>}</div>{mode==='orders'?<div className="market-choice" role="group" aria-label="Фильтр заказов">{([['all','Все'],['purchases','Мои покупки'],['sales','Мои продажи']] as const).map(([id,label])=><button key={id} aria-pressed={orderRoleFilter===id} onClick={()=>setOrderRoleFilter(id)}>{label}</button>)}</div>:<label className="market-sort"><span className="sr-only">Сортировка</span><select aria-label="Сортировка объявлений" value={filters.sort} onChange={e=>setFilters(f=>({...f,sort:e.target.value as MarketFilters['sort']}))}><option value="relevant">Актуальные</option><option value="newest">Сначала новые</option><option value="price-asc">Дешевле</option><option value="price-desc">Дороже</option></select><ChevronDown size={14}/></label>}</div>
 {count>0&&mode!=='orders'&&<div className="market-applied-filters"><span>{filters.city||[filters.condition?conditionName(filters.condition):'',filters.minPrice?'от '+filters.minPrice+' ₽':'',filters.maxPrice?'до '+filters.maxPrice+' ₽':'',filters.verified?'Подтверждённая личность':''].filter(Boolean).join(' · ')||'Фильтры применены'}</span><button onClick={()=>setFilters({...emptyFilters,query:filters.query,sort:filters.sort})}>Сбросить<X size={14}/></button></div>}
 {cachedFavorites&&<p className="market-note">Объявления сохранены на этом устройстве. Откройте товар, чтобы проверить актуальность цены.</p>}
 {mode==='own'&&local.draft&&<div className="market-fixture-notice"><span>Черновик на устройстве: <b>{local.draft.title||'Без названия'}</b> · не отправлен на сервер</span><button onClick={()=>setEditor(true)}>Продолжить черновик<ArrowUpRight size={14}/></button></div>}
 {catalogError&&<div className="market-error-state" role="alert"><p>{catalogError}</p><button className="market-secondary" onClick={()=>setRetry(v=>v+1)}><RefreshCw size={16}/>Повторить</button>{source==='server'&&mode!=='orders'&&<button className="market-draft-link" onClick={()=>{setSource('fixture');setMode('catalog')}}>Посмотреть пример каталога<ArrowUpRight size={16}/></button>}</div>}
 {mode==='orders'?<div className="market-grid" aria-busy={loading}>
  {filteredOrders.map(order=><article key={order.id} className="market-card" data-status={order.status}>
   <button className="market-card-open" onClick={()=>setActiveOrder(order)}>
    <span className="market-card-copy">
     <strong>{money(order.lockedPriceKopecks)}</strong>
     <span className="market-card-title">{order.listingTitle||'Заказ #'+order.id.slice(0,8)}</span>
     <span className="market-card-meta"><span>{order.buyerId===context?.owner?'Покупка':'Продажа'}</span><span>{orderStatusLabel(order.status)}</span></span>
     {order.paymentMode==='sandbox'&&<small>Тестовая оплата — деньги не списываются</small>}
    </span>
   </button>
  </article>)}
  {!loading&&!catalogError&&!filteredOrders.length&&<div className="market-empty"><ShoppingBag size={36} strokeWidth={1.2}/><h3>Заказов пока нет</h3><p>Здесь появятся ваши покупки и продажи с зафиксированными условиями сделки.</p><button className="market-secondary" onClick={()=>setMode('catalog')}>Перейти к каталогу</button></div>}
 </div>:<>
  <div className="market-grid" aria-busy={loading}>{loading&&!page.listings.length?Array.from({length:6},(_,i)=><div key={i} className="market-skeleton" aria-hidden="true"><span/><i/><i/></div>):page.listings.map((listing,i)=><ListingCard key={listing.id} listing={listing} saved={local.favorites.includes(listing.id)} busy={pendingFavorites.has(listing.id)} showActiveStatus={mode==='own'} priority={i<4} onOpen={()=>openListing(listing)} onFavorite={()=>void favorite(listing)}/>)}</div>
  {!loading&&!catalogError&&!page.listings.length&&<div className="market-empty"><PackageSearch size={36} strokeWidth={1.2}/><h3>{mode==='favorites'&&!isFiltered?'Сохраните то, что нравится':mode==='own'?'Ваши объявления появятся здесь':isFiltered?'Ничего не найдено':'Первые предложения — впереди'}</h3><p>{mode==='favorites'&&!isFiltered?'Нажмите на сердце у товара — он останется под рукой.':isFiltered?'Попробуйте другой запрос или уберите часть фильтров.':mode==='own'?'Создайте объявление. Текст и фото сохраняются в черновике до отправки на проверку.': 'Прямые сделки начинаются с первого объявления.'}</p>{isFiltered?<button className="market-secondary" onClick={()=>setFilters(emptyFilters)}>Очистить поиск и фильтры</button>:mode!=='favorites'&&<button className="market-primary" onClick={requestCreate}><Plus size={18}/>Продать товар</button>}{source==='server'&&mode==='catalog'&&!isFiltered&&<button className="market-draft-link" onClick={()=>setSource('fixture')}>Посмотреть пример каталога<ArrowUpRight size={16}/></button>}</div>}
  {page.nextCursor&&<button className="market-load-more market-secondary" disabled={more} onClick={()=>void loadMore()}>{more?'Загружаем…':'Показать ещё'}</button>}
 </>}
 <div className="market-catalog-footer"><span>Прямые сделки. Честные условия.</span><div><button onClick={()=>{if(source==='fixture')setSource('server');setMode(mode==='own'?'catalog':'own')}}>{mode==='own'?'Каталог':'Мои объявления'}<ChevronRight size={15}/></button><button onClick={()=>{if(source==='fixture')setSource('server');setMode(mode==='orders'?'catalog':'orders')}}>{mode==='orders'?'Каталог':'Мои покупки / продажи'}<ChevronRight size={15}/></button></div></div>{local.draft&&<button className="market-resume-draft" onClick={()=>setEditor(true)}><FilePenLine size={20}/><span><b>Продолжить черновик на устройстве</b><small>{local.draft.title||'Новое объявление'}</small></span><ChevronRight size={18}/></button>}
 {missingListing&&<MarketScreen title="Объявление недоступно" onClose={()=>setMissingListing('')}><div className="market-empty"><PackageSearch size={36}/><h3>Не удалось открыть товар</h3><p>{missingListing}</p><button className="market-secondary" onClick={()=>setMissingListing('')}>Вернуться к каталогу</button></div></MarketScreen>}
 {filterOpen&&<MarketplaceFilters value={filters} onApply={setFilters} onClose={()=>setFilterOpen(false)}/>}
 {active&&<ListingDetails key={active.id} listing={active} saved={local.favorites.includes(active.id)} context={context} loading={detailLoading} error={detailError} busy={pendingFavorites.has(active.id)||contactBusy} onClose={closeListing} onFavorite={()=>void favorite(active)} onContact={()=>void contact()} onPurchase={()=>void purchase()} onRefreshLatest={refreshActiveListing} onUpgrade={()=>setPaywall(true)} onUpdate={listing=>{setActive(listing);setPage(p=>({...p,listings:p.listings.map(x=>x.id===listing.id?listing:x)}))}}/>}
 {activeOrder&&<OrderDetailsScreen order={activeOrder} context={context} onClose={()=>setActiveOrder(undefined)} onUpdated={updated=>{setActiveOrder(updated);setOrders(prev=>prev.map(o=>o.id===updated.id?updated:o))}} onOpenListing={id=>{setActiveOrder(undefined);void marketplaceApi.detail(id).then(openListing).catch(e=>toast.error(e instanceof Error?e.message:'Объявление недоступно.'))}} onContactSeller={o=>void contactFromOrder(o)}/>}
 {paywall&&<MarketplacePaywall context={context} error={contextError} onClose={()=>setPaywall(false)} onAccount={()=>{closeListing();onAccount()}} onDraft={()=>{setPaywall(false);closeListing();setEditor(true)}}/>}
 {editor&&<DeferredSurface label="объявление"><ListingEditor key={scope} scope={scope} context={context} data={data} onClose={()=>{setEditor(false);void readMarketLocal(scope).then(setLocal).catch(()=>{})}} onPublished={listing=>{setEditor(false);setSource('server');setMode('own');setRetry(v=>v+1);setActive(listing);setLocal(v=>({...v,draft:undefined}));toast.success('Объявление сохранено на сервере со статусом «Ожидает проверки».') }}/></DeferredSurface>}
 </section>;
}
