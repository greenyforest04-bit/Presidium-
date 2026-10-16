import { useEffect, useState } from 'react'
import type { FeedPost, Story } from '../../../argon/lib/domain'
import type { Listing, MarketOrder, MarketContext } from '../../../argon/lib/commissionka'
import { productRequest, upload, webp } from './api'
import './workspace.css'
import GroupChat from './GroupChat'
import type {MatrixTransport} from '../core/messaging/MatrixTransport'

export type ProductTab = 'Лента'|'Пространства'|'Комиссионка'|'Capsule'|'Профиль'
type Props={tab:ProductTab;userId:string;token:string;openChat:(id:string)=>void;transport:MatrixTransport}
type Profile={userId:string;name:string;handle:string;bio:string;revision:number}
type Account={profile:Profile;devices:{id:string;label:string;current:boolean}[]}
type Space={id:string;kind:'group'|'channel';name:string;description:string;role:string;revision:number;memberCount:number}
type SpaceDetail={space:Space;members:{userId:string;name:string;role:string}[];invites:{id:string;status:string}[]}
type Note={id:string;title:string;text:string;kind:'note'|'task';done:number;revision:number}
type Comment={id:string;post_id:string;user_id:string;name:string;text:string}
type Plan={title:string;priceRub:number|null;dailyAI:number;monthlyAI:number;configured:boolean}
type Subscription={subscription:{tier:string};plans:Record<string,Plan>;usage:{daily:number;monthly:number;dailyLimit:number;monthlyLimit:number};sandboxOrders:boolean}
type Snapshot={account?:Account;feed?:{posts:FeedPost[];stories:Story[];nextCursor:string|null;following:{id:string}[]};spaces?:Space[];listings?:Listing[];own?:Listing[];orders?:MarketOrder[];context?:MarketContext;notes?:Note[];subscription?:Subscription}

function ProtectedImage({path,token,alt}:{path:string;token:string;alt:string}) {
  const [url,setUrl]=useState('')
  useEffect(()=>{let object='',cancelled=false
    void fetch(path,{headers:{Authorization:`Bearer ${token}`}}).then(r=>{if(!r.ok)throw Error();return r.blob()}).then(blob=>{if(!cancelled){object=URL.createObjectURL(blob);setUrl(object)}}).catch(()=>setUrl(''))
    return()=>{cancelled=true;if(object)URL.revokeObjectURL(object)}
  },[path,token])
  return url?<img className="product-image" src={url} alt={alt}/>:null
}

export default function Workspace({tab,userId,token,openChat,transport}:Props) {
  const [data,setData]=useState<Snapshot>({}),[revision,setRevision]=useState(0),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false)
  const draftKey=`presidium_product_post_${userId}`
  const [postText,setPostText]=useState(()=>localStorage.getItem(draftKey)??'')
  const [postId,setPostId]=useState(()=>localStorage.getItem(`${draftKey}_id`)??crypto.randomUUID()),[channel,setChannel]=useState(''),[postMedia,setPostMedia]=useState<string[]>([])
  const [editingPost,setEditingPost]=useState<FeedPost|null>(null)
  const [comments,setComments]=useState<Record<string,Comment[]>>({}),[commentText,setCommentText]=useState('')
  const [spaceEdit,setSpaceEdit]=useState<{name:string;description:string}|null>(null)
  const [spaceName,setSpaceName]=useState(''),[spaceKind,setSpaceKind]=useState<'group'|'channel'>('group'),[spaceDetail,setSpaceDetail]=useState<SpaceDetail|null>(null),[invite,setInvite]=useState(''),[joinToken,setJoinToken]=useState('')
  const [title,setTitle]=useState(''),[description,setDescription]=useState(''),[city,setCity]=useState(''),[price,setPrice]=useState(''),[photo,setPhoto]=useState<File|null>(null),[listingDraft,setListingDraft]=useState(()=>crypto.randomUUID()),[editingListing,setEditingListing]=useState<Listing|null>(null)
  const [note,setNote]=useState<{id:string;title:string;text:string;kind:'note'|'task';done:boolean;revision:number}>(()=>({id:crypto.randomUUID(),title:'',text:'',kind:'note',done:false,revision:0}))
  const [question,setQuestion]=useState(''),[answer,setAnswer]=useState(''),[agentId,setAgentId]=useState(()=>crypto.randomUUID())
  const [profileDraft,setProfileDraft]=useState<{name:string;handle:string;bio:string}|null>(null)
  const [disputeReason,setDisputeReason]=useState('')
  useEffect(()=>localStorage.setItem(draftKey,postText),[draftKey,postText])
  useEffect(()=>localStorage.setItem(`${draftKey}_id`,postId),[draftKey,postId])
  useEffect(()=>{
    let current=true
    const get=<T,>(path:string)=>productRequest<T>(token,path)
    const load=async()=>{
      const [account,subscription,spaces]=await Promise.all([get<Account>('account'),get<Subscription>('subscription'),get<{spaces:Space[]}>('spaces')])
      const next:Snapshot={account,subscription,spaces:spaces.spaces}
      if(tab==='Лента')next.feed=await get('social')
      if(tab==='Комиссионка'){
        const [catalog,own,orders,context]=await Promise.all([get<{listings:Listing[]}>('marketplace'),get<{listings:Listing[]}>('marketplace?view=own'),get<{orders:MarketOrder[]}>('marketplace?view=orders'),get<MarketContext>('marketplace?view=context')])
        if(context.capabilities.moderation){const queue=await get<{listings:Listing[];orders:MarketOrder[]}>("marketplace?view=moderation");own.listings.push(...queue.listings.filter(q=>!own.listings.some(l=>l.id===q.id)));orders.orders.push(...queue.orders.filter(q=>!orders.orders.some(o=>o.id===q.id)))}
        Object.assign(next,{listings:catalog.listings,own:own.listings,orders:orders.orders,context})
      }
      if(tab==='Capsule')next.notes=(await get<{entries:Note[]}>('capsule')).entries
      if(current)setData(next)
    }
    void load().catch(e=>{if(current)setError(String(e.message))})
    return()=>{current=false}
  },[tab,token,revision])
  const refresh=()=>setRevision(v=>v+1)
  const act=async(action:()=>Promise<void>,reload=true)=>{if(busy)return;setBusy(true);setError('');setNotice('');try{await action();if(reload)refresh()}catch(e){setError(e instanceof Error?e.message:'Ошибка операции')}finally{setBusy(false)}}
  const request=<T,>(path:string,body?:unknown)=>productRequest<T>(token,path,body)
  const market=(body:unknown)=>request<{order:MarketOrder}>('marketplace',body)
  const loadComments=async(postId:string)=>{const value=await request<{comments:Comment[]}>(`interactions?postId=${encodeURIComponent(postId)}`);setComments(c=>({...c,[postId]:value.comments}))}
  const selectSpace=async(id:string)=>{setSpaceDetail(await request<SpaceDetail>(`spaces?id=${id}`));setSpaceEdit(null);setInvite('')}
  const money=(value:number)=>(value/100).toLocaleString('ru-RU',{style:'currency',currency:'RUB'})
  const publish=async(story=false)=>{
    if(story)await request('social',{action:'createStory',id:postId,text:postText,mediaIds:postMedia,ttlHours:24})
    else await request('social',editingPost?{action:'updatePost',id:editingPost.id,revision:editingPost.serverRevision,text:postText,mediaIds:postMedia,topics:[]}:{action:'createPost',id:postId,text:postText,mediaIds:postMedia,topics:[],...(channel?{channelId:channel}:{})})
    setPostText('');setPostMedia([]);setPostId(crypto.randomUUID());setEditingPost(null);setNotice(story?'История опубликована на 24 часа':'Публикация сохранена на сервере')
  }
  const createListing=async()=>{
    const priceKopecks=Math.round(Number(price.replace(',','.'))*100)
    if(!Number.isSafeInteger(priceKopecks)||priceKopecks<=0)throw Error('Укажите положительную цену в рублях')
    if(editingListing){
      await market({action:'update',listingId:editingListing.id,revision:editingListing.revision,category:editingListing.category,condition:editingListing.condition,title,description,city,specs:Object.entries(editingListing.specs).map(([name,value])=>({name,value}))})
      setEditingListing(null)
    } else {
      if(!photo)throw Error('Выберите фотографию товара')
      const photoId=listingDraft
      await upload(token,'marketplace',photoId,await webp(photo,1600),'&variant=full')
      await upload(token,'marketplace',photoId,await webp(photo,384),'&variant=thumb')
      await market({action:'create',id:listingDraft,category:'other',condition:'used',sellerType:'private',title,description,city,priceKopecks,photoIds:[photoId],specs:[],acceptedRules:true,conditionDeclaration:true})
    }
    setTitle('');setDescription('');setCity('');setPrice('');setPhoto(null);setListingDraft(crypto.randomUUID());setNotice('Объявление отправлено на модерацию')
  }
  const checkout=async(listing:Listing)=>{
    const key=`presidium_checkout_${userId}_${listing.id}_${listing.revision}`
    const id=localStorage.getItem(key)??crypto.randomUUID();localStorage.setItem(key,id)
    const result=await market({action:'checkout',listingId:listing.id,revision:listing.revision,idempotencyKey:id})
    setNotice(`Заказ ${result.order.id}: ${money(result.order.financials.buyerTotalKopecks)}. Только песочница, деньги не списываются.`)
  }
  return <section className="panel product-workspace" aria-label={tab}>
    <div className="product-heading"><h2>{tab}</h2><button type="button" onClick={refresh}>Обновить</button></div>
    <p className="muted">{data.account?.profile.name??userId} · {data.subscription?.subscription.tier??'…'} · единый Matrix-аккаунт</p>
    {error&&<p className="product-error" role="alert">{error} Ваши поля не очищены. При конфликте обновите данные и повторите с актуальной ревизией.</p>}
    {notice&&<p className="status-box" role="status">{notice}</p>}
    {tab==='Лента'&&<>
      <div className="product-form"><h3>{editingPost?'Редактирование публикации':'Новая публикация или Stories'}</h3>
        <label>Текст публикации<textarea value={postText} onChange={e=>setPostText(e.target.value)}/></label>
        <label>Куда публиковать<select value={channel} onChange={e=>setChannel(e.target.value)}><option value="">Общая лента</option>{data.spaces?.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label>Медиа для публикации<input type="file" accept="image/*,video/mp4,application/pdf,text/plain" onChange={e=>{const file=e.target.files?.[0];if(file)void act(async()=>{const id=crypto.randomUUID();await upload(token,'social',id,file,`&kind=${file.type.startsWith('image/')?'image':file.type.startsWith('video/')?'video':'file'}&name=${encodeURIComponent(file.name)}`);setPostMedia(v=>[...v,id])})}}/></label>
        <p className="muted">Вложений: {postMedia.length}. Публикации и Stories видны другим пользователям; приватная переписка сюда не переносится.</p>
        <div className="product-actions"><button disabled={busy} onClick={()=>void act(()=>publish())}>Опубликовать</button><button disabled={busy||!!editingPost} onClick={()=>void act(()=>publish(true))}>Stories на 24 часа</button></div>
        {editingPost&&<button onClick={()=>{setEditingPost(null);setPostText('');setPostMedia([])}}>Отменить редактирование</button>}
      </div>
      <div className="product-stories">{data.feed?.stories.map(s=><article key={s.id}><strong>{s.name}</strong><p>{s.text}</p>{s.imageSrc&&<ProtectedImage path={s.imageSrc} token={token} alt="История"/>}{s.authorId===userId&&<button disabled={busy} onClick={()=>void act(async()=>{await request('social',{action:'deleteStory',id:s.id})})}>Удалить историю</button>}</article>)}</div>
      {data.feed?.posts.map(post=><article className="product-card" key={post.id}><strong>{post.author}</strong><small> · {post.channelId?'Пространство':'Общая лента'}</small><p className="preformatted">{post.text}</p>
        {post.attachments?.map(m=>m.kind==='image'?<ProtectedImage key={m.id} path={`/api/social/media?id=${m.id}`} token={token} alt={m.alt??m.name}/>:<button key={m.id} onClick={()=>void act(async()=>{const r=await fetch(`/api/social/media?id=${m.id}`,{headers:{Authorization:`Bearer ${token}`}});if(!r.ok)throw Error('Файл недоступен');const url=URL.createObjectURL(await r.blob());const link=document.createElement('a');link.href=url;link.download=m.name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)})}>Скачать {m.name}</button>)}
        <div className="product-actions"><button onClick={()=>void act(()=>loadComments(post.id))}>Комментарии</button>{post.authorId!==userId&&<button disabled={busy} onClick={()=>void act(async()=>{await request('interactions',{action:'follow',targetId:post.authorId,following:!data.feed?.following.some(f=>f.id===post.authorId)})})}>{data.feed?.following.some(f=>f.id===post.authorId)?'Отписаться':'Подписаться на автора'}</button>}
        {post.authorId===userId&&<><button onClick={()=>{setEditingPost(post);setPostText(post.text);setPostMedia(post.attachments?.map(m=>m.id)??[])}}>Редактировать</button><button disabled={busy} onClick={()=>void act(async()=>{await request('social',{action:'deletePost',id:post.id,revision:post.serverRevision})})}>Удалить публикацию</button></>}</div>
        {comments[post.id]&&<div>{comments[post.id].map(c=><p key={c.id}>{c.name}: {c.text}{c.user_id===userId&&<button disabled={busy} onClick={()=>void act(async()=>{await request('interactions',{action:'deleteComment',id:c.id,postId:post.id});await loadComments(post.id)})}>Удалить комментарий</button>}</p>)}<label>Текст комментария<input value={commentText} onChange={e=>setCommentText(e.target.value)}/></label><button disabled={busy} onClick={()=>void act(async()=>{await request('interactions',{action:'comment',id:crypto.randomUUID(),postId:post.id,text:commentText});setCommentText('');await loadComments(post.id)})}>Добавить комментарий</button></div>}
      </article>)}
      {data.feed?.nextCursor&&<button disabled={busy} onClick={()=>void act(async()=>{const next=await request<NonNullable<Snapshot['feed']>>(`social?cursor=${encodeURIComponent(data.feed!.nextCursor!)}`);setData(d=>({...d,feed:{...next,posts:[...(d.feed?.posts??[]),...next.posts]}}))},false)}>Ещё публикации</button>}
    </>}
    {tab==='Пространства'&&<>
      <GroupChat transport={transport}/>
      <p>Группы и каналы с ролями и приглашениями. Публикации пространства находятся в разделе «Лента»; личная переписка работает через Matrix.</p>
      <label>Название пространства<input value={spaceName} onChange={e=>setSpaceName(e.target.value)}/></label><label>Вид пространства<select value={spaceKind} onChange={e=>setSpaceKind(e.target.value as 'group'|'channel')}><option value="group">Группа</option><option value="channel">Канал</option></select></label>
      <button disabled={busy} onClick={()=>void act(async()=>{const result=await request<{spaceId:string}>('spaces',{action:'create',id:crypto.randomUUID(),kind:spaceKind,name:spaceName,description:''});setSpaceName('');await selectSpace(result.spaceId)})}>Создать пространство</button>
      <label>Код приглашения<input value={joinToken} onChange={e=>setJoinToken(e.target.value)}/></label><button disabled={busy} onClick={()=>void act(async()=>{await request('spaces',{action:'join',token:joinToken});setJoinToken('')})}>Вступить</button>
      {data.spaces?.map(s=><article className="product-card" key={s.id}><strong>{s.name}</strong> · {s.kind} · {s.role} · участников {s.memberCount}<button onClick={()=>void act(()=>selectSpace(s.id))}>Участники {s.name}</button></article>)}
      {spaceDetail&&<article className="product-card"><h3>{spaceDetail.space.name}</h3>{spaceDetail.members.map(m=><div key={m.userId}>{m.name}: {m.role}{spaceDetail.space.role==='owner'&&m.role!=='owner'&&<div className="product-actions"><button onClick={()=>void act(async()=>{await request('spaces',{action:'role',spaceId:spaceDetail.space.id,userId:m.userId,role:m.role==='admin'?'member':'admin'});await selectSpace(spaceDetail.space.id)})}>Изменить роль</button><button onClick={()=>void act(async()=>{await request('spaces',{action:'remove',spaceId:spaceDetail.space.id,userId:m.userId});await selectSpace(spaceDetail.space.id)})}>Исключить</button></div>}</div>)}
        {spaceDetail.space.role!=='member'&&<div>
          <label>Название выбранного пространства<input value={spaceEdit?.name??spaceDetail.space.name} onChange={e=>setSpaceEdit({description:spaceDetail.space.description,...spaceEdit,name:e.target.value})}/></label>
          <label>Описание пространства<textarea value={spaceEdit?.description??spaceDetail.space.description} onChange={e=>setSpaceEdit({name:spaceDetail.space.name,...spaceEdit,description:e.target.value})}/></label>
          <button disabled={busy} onClick={()=>void act(async()=>{await request('spaces',{action:'update',spaceId:spaceDetail.space.id,revision:spaceDetail.space.revision,...(spaceEdit??{name:spaceDetail.space.name,description:spaceDetail.space.description})});await selectSpace(spaceDetail.space.id)})}>Сохранить пространство</button>
          {spaceDetail.invites.filter(i=>i.status==='active').map(i=><p key={i.id}>Активное приглашение {i.id.slice(0,8)} <button disabled={busy} onClick={()=>void act(async()=>{await request('spaces',{action:'revokeInvite',spaceId:spaceDetail.space.id,inviteId:i.id});await selectSpace(spaceDetail.space.id)})}>Отозвать приглашение</button></p>)}
        </div>}
        {spaceDetail.space.role!=='member'&&<button onClick={()=>void act(async()=>{const result=await request<{token:string}>('spaces',{action:'invite',spaceId:spaceDetail.space.id});setInvite(result.token)})}>Создать приглашение на 24 часа</button>}
        {invite&&<label>Приглашение<input readOnly value={invite}/></label>}
        {spaceDetail.space.role!=='owner'&&<button onClick={()=>void act(async()=>{await request('spaces',{action:'leave',spaceId:spaceDetail.space.id});setSpaceDetail(null)})}>Выйти из пространства</button>}
      </article>}
    </>}
    {tab==='Комиссионка'&&<>
      <p className="status-box">Тестовые заказы: реальные платежи не подключены, деньги не списываются. Объявления публикуются после модерации.</p>
      <div className="product-form"><h3>{editingListing?'Редактирование объявления':'Новое объявление'}</h3><label>Название товара<input value={title} onChange={e=>setTitle(e.target.value)}/></label><label>Описание товара<textarea value={description} onChange={e=>setDescription(e.target.value)}/></label><label>Город<input value={city} onChange={e=>setCity(e.target.value)}/></label><label>Цена, рубли<input inputMode="decimal" value={price} onChange={e=>setPrice(e.target.value)}/></label>{!editingListing&&<label>Фото товара<input type="file" accept="image/jpeg,image/png,image/webp" onChange={e=>setPhoto(e.target.files?.[0]??null)}/></label>}{editingListing&&<div className="product-actions"><button disabled={busy} onClick={()=>void act(async()=>{const amount=Math.round(Number(price.replace(',','.'))*100);if(!Number.isSafeInteger(amount)||amount<=0)throw Error('Укажите положительную цену');await market({action:'price',listingId:editingListing.id,revision:editingListing.revision,priceKopecks:amount});setEditingListing(null);setTitle('');setDescription('');setCity('');setPrice('')})}>Сохранить новую цену</button><button onClick={()=>{setEditingListing(null);setTitle('');setDescription('');setCity('');setPrice('')}}>Отменить редактирование объявления</button></div>}<p className="muted">Размещая б/у товар, подтверждаете его использование и право продажи. Для новой категории или производителя требуется отдельная проверка.</p><button disabled={busy} onClick={()=>void act(createListing)}>{editingListing?'Сохранить объявление':'Отправить на модерацию'}</button></div>
      <h3>Каталог</h3>{data.listings?.length===0&&<p>Опубликованных объявлений пока нет.</p>}
      {[...(data.listings??[]),...(data.own??[]).filter(l=>!data.listings?.some(x=>x.id===l.id))].map(l=><article className="product-card" key={l.id}><h3>{l.title}</h3><p>{l.description}</p><strong>{money(l.priceKopecks)}</strong> · {l.city} · {l.status}<ProtectedImage path={l.images[0]?.thumbnail??''} token={token} alt={l.title}/><p>Продавец: {l.seller.name}</p>
        {l.seller.id!==userId&&l.status==='active'&&<div className="product-actions"><button disabled={busy} onClick={()=>void act(()=>checkout(l))}>Оформить тестовый заказ</button><button onClick={()=>openChat(l.seller.id)}>Написать продавцу</button><button disabled={busy} onClick={()=>void act(async()=>{await market({action:'favorite',listingId:l.id,saved:!data.context?.favoriteIds?.includes(l.id)})})}>{data.context?.favoriteIds?.includes(l.id)?'Убрать из избранного':'В избранное'}</button></div>}
        {l.seller.id===userId&&l.status!=='sold'&&<div className="product-actions"><button onClick={()=>{setEditingListing(l);setTitle(l.title);setDescription(l.description);setCity(l.city);setPrice(String(l.priceKopecks/100))}}>Изменить объявление</button><button disabled={busy} onClick={()=>void act(async()=>{await market({action:'withdraw',listingId:l.id,revision:l.revision})})}>Снять объявление</button></div>}
        {data.context?.capabilities.moderation&&l.status==='pending'&&<button disabled={busy} onClick={()=>void act(async()=>{await market({action:'moderateListing',listingId:l.id,revision:l.revision,decision:'approve',conditionVerified:true,rightToSellVerified:true,note:'Проверено локальным модератором'})})}>Одобрить объявление</button>}
      </article>)}
      <h3>Заказы и споры</h3><label>Причина спора / решение модератора<textarea value={disputeReason} onChange={e=>setDisputeReason(e.target.value)}/></label>
      {data.orders?.map(o=><article className="product-card" key={o.id}><strong>{o.listingTitle}</strong><p>Заказ {o.id} · {o.status}</p><p>Зафиксировано: товар {money(o.financials.priceKopecks)} + доставка {money(o.financials.deliveryKopecks)} + оператор {money(o.financials.paymentOperatorKopecks)} = <strong>{money(o.financials.buyerTotalKopecks)}</strong></p><p>Комиссия продавца: {money(o.financials.commissionKopecks)}; продавцу {money(o.financials.sellerNetKopecks)}</p>
        <div className="product-actions">{o.buyerId===userId&&o.status==='confirmed'&&<button disabled={busy} onClick={()=>void act(async()=>{await market({action:'paySandbox',orderId:o.id})})}>Тестовая оплата без списания</button>}{o.sellerId===userId&&o.status==='paid_sandbox'&&<button disabled={busy} onClick={()=>void act(async()=>{await market({action:'orderStatus',orderId:o.id,status:'shipped'})})}>Отметить отправку</button>}{o.buyerId===userId&&o.status==='shipped'&&<button disabled={busy} onClick={()=>void act(async()=>{await market({action:'orderStatus',orderId:o.id,status:'completed'})})}>Подтвердить получение</button>}{['paid_sandbox','paid','shipped'].includes(o.status)&&<button disabled={busy} onClick={()=>void act(async()=>{await market({action:'dispute',orderId:o.id,reason:disputeReason})})}>Открыть спор</button>}{o.status==='confirmed'&&<button disabled={busy} onClick={()=>void act(async()=>{await market({action:'orderStatus',orderId:o.id,status:'cancelled'})})}>Отменить неоплаченный заказ</button>}</div>
        {o.dispute&&<p>Спор: {o.dispute.reason} · {o.dispute.status}</p>}{data.context?.capabilities.moderation&&o.dispute?.status==='open'&&<div className="product-actions"><button disabled={busy} onClick={()=>void act(async()=>{await market({action:'resolveDispute',disputeId:o.dispute!.id,decision:'refund_buyer',resolutionNote:disputeReason})})}>Решение в пользу покупателя</button><button disabled={busy} onClick={()=>void act(async()=>{await market({action:'resolveDispute',disputeId:o.dispute!.id,decision:'release_seller',resolutionNote:disputeReason})})}>Решение в пользу продавца</button></div>}
      </article>)}
    </>}
    {tab==='Capsule'&&<>
      <label>Название записи<input value={note.title} onChange={e=>setNote(n=>({...n,title:e.target.value}))}/></label><label>Текст записи<textarea value={note.text} onChange={e=>setNote(n=>({...n,text:e.target.value}))}/></label><label>Тип записи<select value={note.kind} onChange={e=>setNote(n=>({...n,kind:e.target.value as 'note'|'task'}))}><option value="note">Заметка</option><option value="task">Задача</option></select></label><button disabled={busy} onClick={()=>void act(async()=>{await request('capsule',note);setNote({id:crypto.randomUUID(),title:'',text:'',kind:'note',done:false,revision:0});setNotice('Запись сохранена')})}>Сохранить запись</button>
      {data.notes?.map(n=><article className="product-card" key={n.id}><h3>{n.title}</h3><p className="preformatted">{n.text}</p><div className="product-actions"><button onClick={()=>setNote({id:n.id,title:n.title,text:n.text,kind:n.kind,done:!!n.done,revision:n.revision})}>Открыть запись</button>{n.kind==='task'&&<button disabled={busy} onClick={()=>void act(async()=>{await request('capsule',{id:n.id,title:n.title,text:n.text,kind:n.kind,done:!n.done,revision:n.revision})})}>{n.done?'Возобновить задачу':'Завершить задачу'}</button>}<button disabled={busy} onClick={()=>void act(async()=>{await request('capsule',{id:n.id,title:n.title,text:n.text,kind:n.kind,done:!!n.done,revision:n.revision,deleted:true})})}>Удалить запись</button></div></article>)}
      <h3>Облачный AI</h3><p>Передаются только вопрос и открытая запись Capsule. Переписка и ключи Matrix не передаются.</p><label>Вопрос AI<textarea value={question} onChange={e=>{setQuestion(e.target.value);setAgentId(crypto.randomUUID())}}/></label><button disabled={busy} onClick={()=>void act(async()=>{const value=await request<{answer:string}>('capsule-agent',{requestId:agentId,question,materials:note.text?[{id:note.id,title:note.title,text:note.text.slice(0,600)}]:[]});setAnswer(value.answer)})}>Спросить AI</button>{answer&&<p className="preformatted">{answer}</p>}
    </>}
    {tab==='Профиль'&&data.account&&<>
      <label>Имя профиля<input value={profileDraft?.name??data.account.profile.name} onChange={e=>setProfileDraft({...data.account!.profile,...profileDraft,name:e.target.value})}/></label><label>Публичное имя<input value={profileDraft?.handle??data.account.profile.handle} onChange={e=>setProfileDraft({...data.account!.profile,...profileDraft,handle:e.target.value})}/></label><label>О себе<textarea value={profileDraft?.bio??data.account.profile.bio} onChange={e=>setProfileDraft({...data.account!.profile,...profileDraft,bio:e.target.value})}/></label><button disabled={busy} onClick={()=>void act(async()=>{await request('account',{...(profileDraft??data.account!.profile),userId:undefined,updatedAt:undefined,revision:data.account!.profile.revision});setProfileDraft(null)})}>Сохранить профиль</button>
      <h3>Устройства Matrix</h3>{data.account.devices.map(d=><p key={d.id}>{d.label} {d.current?'· это устройство':''}{!d.current&&<button onClick={()=>void act(async()=>{await request('account',{action:'revoke',sessionId:d.id})})}>Отозвать доступ к функциям продукта</button>}</p>)}<p className="muted">Сеанс самого Matrix управляется homeserver. Здесь отзывается доступ устройства к профилю, заказам и Capsule.</p>
      <h3>Тарифы</h3><p>Текущий тариф: {data.subscription?.subscription.tier}. Изменение тарифа возможно только по подписанному событию сервера оплаты.</p>{Object.entries(data.subscription?.plans??{}).map(([id,p])=><p key={id}><strong>{p.title}</strong> · {p.configured?`${p.priceRub} ₽/месяц; AI ${p.dailyAI}/день, ${p.monthlyAI}/месяц`:'не подключён'}</p>)}<p>AI: {data.subscription?.usage.daily}/{data.subscription?.usage.dailyLimit} сегодня, {data.subscription?.usage.monthly}/{data.subscription?.usage.monthlyLimit} за месяц.</p>
    </>}
  </section>
}
