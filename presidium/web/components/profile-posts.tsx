'use client';
import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,Bookmark,CloudUpload,Grid3X3,Heart,ImagePlus,Layers,LayoutList,Maximize2,MessageCircle,Pencil,Trash2} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from './ui/dialog';
import {PostAsset,SocialAvatar,SocialMenu} from './social-ui';
import {PostSaveSheet} from './post-save-sheet';
import {profilePosts} from '@/lib/profile-content';
import {attachmentsOf,putFeedPost,removeFeedPost} from '@/lib/feed';
import {read,type FeedPost,type State} from '@/lib/presidium';
import {discardPostDraft} from '@/lib/publication-writer';
import {socialApi,isOwnSocialPost} from '@/lib/social-sync';
import type {CommitState} from '@/lib/people';
import {toast} from 'sonner';

function PostTile({post,onOpen}:{post:FeedPost;onOpen:()=>void}){
 const first=attachmentsOf(post).find(a=>a.kind!=='file'),remote=post.images?.[0],[url,setUrl]=useState(remote||''),[failed,setFailed]=useState(false);
 useEffect(()=>{let live=true,owned='';setUrl(remote||'');setFailed(false);if(first)void read<Blob>('blobs',first.blobId).then(b=>{if(!live)return;if(b){owned=URL.createObjectURL(b);setUrl(owned)}else if(!remote)setFailed(true)}).catch(()=>{if(live&&!remote)setFailed(true)});return()=>{live=false;if(owned)URL.revokeObjectURL(owned)}},[first?.blobId,remote]);
 const count=attachmentsOf(post).length||post.images?.length||0;
 return <button className={'profile-post-tile '+(!count?'text-only':'')} onClick={onOpen} aria-label={'Открыть публикацию: '+(post.text.slice(0,100)||'Фото и видео')}>
  {url&&!failed?(first?.kind==='video'?<video src={url} muted playsInline preload="metadata" onError={()=>setFailed(true)}/>:<img src={url} alt={first?.alt||''} loading="lazy" onError={()=>setFailed(true)}/> ):<span>{failed?<ImagePlus size={22}/>:<Pencil size={18}/>}<b>{post.text||attachmentsOf(post)[0]?.name||'Публикация'}</b></span>}
  {count>1&&<i><Layers size={15}/>{count}</i>}{first?.kind==='video'&&<small>Видео</small>}
 </button>;
}

export function ProfilePosts({data,commit,serverPosts=[],viewerId,onServerPostSynced,onServerPostRemoved,onCreate,onEdit}:{data:State;commit:CommitState;serverPosts?:FeedPost[];viewerId?:string|null;onServerPostSynced?:(post:FeedPost)=>void;onServerPostRemoved?:(id:string)=>void;onCreate:()=>void;onEdit:(p:FeedPost)=>void}){
 const [layout,setLayout]=useState<'list'|'grid'>('grid'),[filter,setFilter]=useState('all'),[opened,setOpened]=useState<string|null>(null),[commentId,setCommentId]=useState<string|null>(null),[saved,setSaved]=useState<FeedPost|null>(null),[pending,setPending]=useState<string|null>(null),[draftBusy,setDraftBusy]=useState(false),[confirmPublish,setConfirmPublish]=useState<FeedPost|null>(null),[syncBusy,setSyncBusy]=useState(false);
 const localOwn=profilePosts(data),ownServer=serverPosts.filter(sp=>isOwnSocialPost(sp,viewerId,data.feedPosts));
 const seenIds=new Set<string>();
 const posts=[...ownServer.map(sp=>{const loc=localOwn.find(l=>l.id===sp.id);return loc?{...loc,...sp,attachments:loc.attachments?.length?loc.attachments:sp.attachments}:sp}),...localOwn].filter(p=>{if(seenIds.has(p.id))return false;seenIds.add(p.id);return true}).sort((a,b)=>b.createdAt-a.createdAt);
 const shown=posts.filter(p=>filter==='personal'?!p.channelId:filter==='channels'?!!p.channelId:filter==='video'?attachmentsOf(p).some(a=>a.kind==='video'):true);
 const active=posts.find(p=>p.id===opened),commentPost=posts.find(p=>p.id===commentId),draft=data.drafts['feed-composer']?.trim()||(data.postDrafts?.new?.attachments.length?'Черновик с вложениями':'');
 const mounted=useRef(true);useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 const date=(p:FeedPost)=>new Date(p.createdAt).toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:new Date(p.createdAt).getFullYear()!==new Date().getFullYear()?'numeric':undefined});
 const location=(p:FeedPost)=>p.channelId?data.spaces.find(s=>s.id===p.channelId)?.name||p.author:'Моя лента';
 async function toggleLike(id:string){if(pending)return;setPending(id);try{await commit(s=>({...s,feedLikes:s.feedLikes?.includes(id)?s.feedLikes.filter(x=>x!==id):[...(s.feedLikes||[]),id]}))}catch{toast.error('Не удалось сохранить реакцию')}finally{if(mounted.current)setPending(null)}}
 async function deleteDraft(){
  if(draftBusy)return;setDraftBusy(true);const text=data.drafts['feed-composer']||'',savedDraft=data.postDrafts?.new;
  try{await commit(s=>discardPostDraft(s,'new'));toast('Черновик удалён',{action:{label:'Отменить',onClick:()=>{void commit(s=>{if(s.postDrafts?.new||s.drafts['feed-composer'])throw Error('Уже есть новый черновик');return {...s,postDrafts:savedDraft?{...s.postDrafts,new:savedDraft}:s.postDrafts,drafts:{...s.drafts,'feed-composer':text}}}).catch(()=>toast.error('Не удалось восстановить черновик: уже есть новый или хранилище недоступно'))}}})}catch{toast.error('Черновик не удалён')}finally{if(mounted.current)setDraftBusy(false)}
 }
 async function remove(post:FeedPost){
  if(pending)return;setPending(post.id);
  try{
   if(post.syncStatus==='server'){
    await socialApi.act({action:'deletePost',id:post.id,revision:post.serverRevision});
    onServerPostRemoved?.(post.id);
   }
   await commit(s=>removeFeedPost(s,post.id));
   setOpened(null);setCommentId(null);
   toast(post.syncStatus==='server'?'Пост удалён с сервера и из профиля':'Пост удалён из локального профиля',{
    ...(post.syncStatus!=='server'?{action:{label:'Отменить',onClick:async()=>{try{await commit(s=>({...putFeedPost(s,post),drafts:s.drafts,postDrafts:s.postDrafts}));toast.success('Локальная публикация восстановлена')}catch{toast.error('Не удалось восстановить пост')}}}}:{})
   });
  }catch(e){toast.error(e instanceof Error?e.message:'Публикация не удалена. Попробуйте снова.')}finally{if(mounted.current)setPending(null)}
 }
 function menu(post:FeedPost){return <SocialMenu label="Действия с моей публикацией" items={[
  ...(!post.demo&&post.syncStatus!=='server'?[{label:'Опубликовать на сервере',icon:<CloudUpload size={16}/>,action:()=>{setOpened(null);setConfirmPublish(post)}}]:[]),
  {label:'Редактировать',icon:<Pencil size={16}/>,action:()=>{setOpened(null);onEdit(post)}},
  {label:'На весь экран',icon:<Maximize2 size={16}/>,action:()=>setOpened(post.id)},
  {label:'В капсулу',icon:<Bookmark size={16}/>,action:()=>setSaved(post)},
  {label:'Удалить пост',icon:<Trash2 size={16}/>,danger:true,separator:true,action:()=>void remove(post)},
 ]}/>}
 function actions(post:FeedPost){return <div className="profile-post-actions"><button disabled={pending===post.id} aria-label={data.feedLikes?.includes(post.id)?'Убрать реакцию':'Нравится публикация'} aria-pressed={!!data.feedLikes?.includes(post.id)} onClick={()=>void toggleLike(post.id)}><Heart size={21} fill={data.feedLikes?.includes(post.id)?'currentColor':'none'}/>{data.feedLikes?.includes(post.id)&&<small>1</small>}</button><button aria-label="Открыть комментарии" onClick={()=>setCommentId(post.id)}><MessageCircle size={21}/><small>{data.feedComments?.[post.id]?.length||''}</small></button><button className="profile-post-save" aria-label="Сохранить пост в капсулу" aria-pressed={!!data.feedSaved?.includes(post.id)} onClick={()=>setSaved(post)}><Bookmark size={20} fill={data.feedSaved?.includes(post.id)?'currentColor':'none'}/></button></div>}
 return <section className="profile-posts" aria-label="Мои публикации">
  {draft&&<div className="profile-post-draft"><button onClick={onCreate}><Pencil size={18}/><span><b>Продолжить черновик на устройстве</b><small>{draft.slice(0,90)}</small></span><ArrowRight size={18}/></button><button aria-label="Удалить черновик публикации" disabled={draftBusy} onClick={()=>void deleteDraft()}><Trash2 size={17}/></button></div>}
  {posts.length>0?<><div className="profile-post-toolbar"><span>Мои публикации</span><div><button aria-label="Публикации списком" aria-pressed={layout==='list'} onClick={()=>setLayout('list')}><LayoutList size={19}/></button><button aria-label="Публикации сеткой" aria-pressed={layout==='grid'} onClick={()=>setLayout('grid')}><Grid3X3 size={18}/></button></div></div>
   {(posts.some(p=>p.channelId)||posts.some(p=>attachmentsOf(p).some(a=>a.kind==='video')))&&<div className="profile-post-filters">{[['all','Все'],['personal','Моя лента'],...(posts.some(p=>p.channelId)?[['channels','Каналы']]:[]),...(posts.some(p=>attachmentsOf(p).some(a=>a.kind==='video'))?[['video','Видео']]:[])].map(([id,label])=><button key={id} aria-pressed={filter===id} onClick={()=>setFilter(id)}>{label}</button>)}</div>}
   {shown.length?(layout==='grid'?<div className="profile-post-grid">{shown.map(p=><PostTile key={p.id} post={p} onOpen={()=>setOpened(p.id)}/>)}</div>:<div className="profile-post-list">{shown.map(p=><article className="profile-post" key={p.id}><header><SocialAvatar name={data.profile} blobId={data.avatarId} me/><span><b>{p.channelId?location(p):data.profile}</b><small>{date(p)} · {p.channelId?'Канал':'Моя лента'} · {p.syncStatus==='server'?`Опубликовано на сервере (рев. #${p.serverRevision||1})`:'Сохранено на устройстве'}{p.updatedAt?' · изменено':''}</small></span>{menu(p)}</header>{p.text&&<p className="profile-post-text">{p.text}</p>}<PostAsset post={p} onOpen={()=>setOpened(p.id)}/>{actions(p)}</article>)}</div>):<div className="profile-post-filter-empty"><p>В этой категории пока нет постов.</p><button onClick={()=>setFilter('all')}>Показать все</button></div>}
  </>:<div className="profile-post-empty"><span><ImagePlus size={24}/></span><div><h3>Здесь будут ваши публикации</h3><p>Начните с фотографии или мысли,<br/>которой хочется поделиться.</p></div></div>}
  {saved&&<PostSaveSheet post={saved} data={data} commit={commit} onClose={()=>setSaved(null)}/>}
  {confirmPublish&&<Dialog open onOpenChange={v=>{if(!v&&!syncBusy)setConfirmPublish(null)}}><DialogContent customPosition className="pc-exit" showCloseButton={false}><DialogTitle>Опубликовать пост на сервере?</DialogTitle><DialogDescription>Выбранная публикация и её вложения будут переданы на сервер Presidium. Локальная копия на устройстве сохранится. Требуется активная подписка Plus или Business.</DialogDescription><button disabled={syncBusy} onClick={async()=>{setSyncBusy(true);try{const published=await socialApi.publishLocalPostToServer(confirmPublish);const merged={...confirmPublish,authorId:published.authorId,syncStatus:'server' as const,serverRevision:published.serverRevision||1};await commit(s=>putFeedPost(s,merged));onServerPostSynced?.(published);setConfirmPublish(null);toast.success('Пост опубликован на сервере.')}catch(e){toast.error(e instanceof Error?e.message:'Не удалось опубликовать на сервере.')}finally{setSyncBusy(false)}}}>{syncBusy?'Публикуем…':'Подтвердить публикацию'}</button><button className="pc-exit-continue" disabled={syncBusy} onClick={()=>setConfirmPublish(null)}>Отмена</button></DialogContent></Dialog>}
  <Dialog open={!!active} onOpenChange={v=>{if(!v)setOpened(null)}}><DialogContent customPosition className="feed-fullscreen profile-post-full" showCloseButton={false}><DialogTitle className="sr-only">Моя публикация</DialogTitle><DialogDescription className="sr-only">Просмотр публикации, карусели фото или видео.</DialogDescription>{active&&<><header><button className="glass-action" aria-label="Закрыть публикацию" onClick={()=>setOpened(null)}><ArrowLeft/></button><span><b>{location(active)}</b><small>{date(active)} · {active.syncStatus==='server'?`На сервере (рев. #${active.serverRevision||1})`:'На устройстве'}</small></span>{menu(active)}</header><div className="feed-fullbody"><PostAsset post={active} full/><p>{active.text}</p>{actions(active)}</div><footer><button className="icon-btn" aria-label="Предыдущая публикация" disabled={posts.findIndex(p=>p.id===active.id)<=0} onClick={()=>setOpened(posts[posts.findIndex(p=>p.id===active.id)-1]?.id||null)}><ArrowLeft/></button><span>{posts.findIndex(p=>p.id===active.id)+1} / {posts.length}</span><button className="icon-btn" aria-label="Следующая публикация" disabled={posts.findIndex(p=>p.id===active.id)>=posts.length-1} onClick={()=>setOpened(posts[posts.findIndex(p=>p.id===active.id)+1]?.id||null)}><ArrowRight/></button></footer></>}</DialogContent></Dialog>
  {commentPost&&<ProfilePostComments key={commentPost.id} post={commentPost} data={data} commit={commit} onClose={()=>setCommentId(null)}/>}
 </section>;
}

function ProfilePostComments({post,data,commit,onClose}:{post:FeedPost;data:State;commit:CommitState;onClose:()=>void}){
 const [reply,setReply]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const comments=data.feedComments?.[post.id]||[];
 async function perform(change:(s:State)=>State){if(busy)return false;setBusy(true);setError('');try{await commit(change);return true}catch{setError('Комментарий не сохранён. Попробуйте ещё раз.');return false}finally{setBusy(false)}}
 return <Dialog open onOpenChange={v=>{if(!v&&!busy)onClose()}}><DialogContent customPosition className="screen-editor comments-editor profile-post-comments" showCloseButton={false}><header className="editor-top"><button className="glass-action" aria-label="Закрыть комментарии" disabled={busy} onClick={onClose}><ArrowLeft/></button><DialogTitle>Комментарии</DialogTitle><span>{comments.length}</span></header><div className="editor-scroll"><DialogDescription className="sr-only">Комментарии к вашей публикации, сохранённые на этом устройстве.</DialogDescription><div className="comment-original"><b>{post.author}</b><p>{post.text||'Фото и видео'}</p></div>{error&&<p className="people-error" role="alert">{error}</p>}{comments.map((text,index)=><article className="profile-comment" key={index}><SocialAvatar name={data.profile} blobId={data.avatarId} me/><div><b>{data.profile}</b><p>{text}</p></div><button className="icon-btn" aria-label="Удалить свой комментарий" disabled={busy} onClick={()=>void perform(s=>({...s,feedComments:{...s.feedComments,[post.id]:(s.feedComments?.[post.id]||[]).filter((_,i)=>i!==index)}}))}><Trash2 size={16}/></button></article>)}{comments.length===0&&<p className="comments-empty">Обсуждение начинается с первого комментария (сохраняется локально на устройстве).</p>}</div><form className="comment-compose" onSubmit={async e=>{e.preventDefault();const text=reply.trim();if(!text||busy)return;if(await perform(s=>({...s,feedComments:{...s.feedComments,[post.id]:[...(s.feedComments?.[post.id]||[]),text]}})))setReply('')}}><input aria-label="Текст комментария" placeholder="Написать комментарий…" maxLength={1000} disabled={busy} value={reply} onChange={e=>setReply(e.target.value)}/><button className="primary-btn" disabled={busy||!reply.trim()}>Отправить</button></form></DialogContent></Dialog>;
}
