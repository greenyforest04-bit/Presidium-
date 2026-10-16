'use client';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {X,ImagePlus,Video,Paperclip,Eye,ChevronLeft,ChevronDown,Lock,LoaderCircle,Check,CloudOff,Info,ArrowUpRight,CloudUpload,RefreshCw} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from './ui/dialog';
import {Progress} from './ui/progress';
import {SocialAvatar} from './social-ui';
import {State,FeedPost,recordId,read} from '@/lib/presidium';
import {attachmentsOf,POST_DOCUMENT_ACCEPT,validatePublicationFiles,putFeedPost} from '@/lib/feed';
import {PublicationGallery,PublicationMedia,PublicationMediaManager,choiceKind} from './publication-media';
import {type PublicationChoice,discardPostDraft} from '@/lib/publication-writer';
import {publicationSession,type PublicationSession} from '@/lib/publication-session';
import {usePublicationDraft} from '@/hooks/use-publication-draft';
import {useComposerCaret} from '@/hooks/use-composer-caret';
import {socialApi,SocialSyncError} from '@/lib/social-sync';
import type {CommitState} from '@/lib/people';
import {toast} from 'sonner';

export function PostComposer({post,data,commit,onClose,onPublished}:{post?:FeedPost;data:State;commit:CommitState;setData:React.Dispatch<React.SetStateAction<State>>;onClose:()=>void;onPublished?:(post:FeedPost)=>void}){
 const draftKey=post?.id||'new',loaded=data.postDrafts?.[draftKey];
 const isServerPost=post?.syncStatus==='server';
 const [text,setText]=useState(post?loaded?.text??post.text:loaded?.text??data.drafts['feed-composer']??''),[target,setTarget]=useState(loaded?.channelId??post?.channelId??''),[media,setMedia]=useState<PublicationChoice[]>(()=>((loaded?.attachments??attachmentsOf(post)).map(a=>({key:a.id,attachment:a,alt:a.alt||''})))),[publishScope,setPublishScope]=useState<'local'|'server'>(isServerPost?'server':'local'),[serverRev,setServerRev]=useState<number>(post?.serverRevision||1),[conflictServerPost,setConflictServerPost]=useState<FeedPost|null>(null),[busy,setBusy]=useState(false),[progress,setProgress]=useState(0),[error,setError]=useState(''),[confirm,setConfirm]=useState(false),[preview,setPreview]=useState(false),[dropping,setDropping]=useState(false),[operation,setOperation]=useState<'publish'|'draft'|'discard'>('publish');
 const session=useRef<PublicationSession|null>(null);if(!session.current)session.current=publicationSession(commit,recordId);
 const publicationId=useRef(loaded?.id||post?.id||recordId()),textarea=useRef<HTMLTextAreaElement>(null),scroll=useRef<HTMLDivElement>(null),photoPicker=useRef<HTMLInputElement>(null),videoPicker=useRef<HTMLInputElement>(null),documentPicker=useRef<HTMLInputElement>(null),closing=useRef(false),working=useRef(false);
 const initial=useRef(JSON.stringify({text,target,media})),changed=JSON.stringify({text,target,media})!==initial.current;
 const draft=usePublicationDraft({id:publicationId.current,draftKey,post,text,channelId:target||undefined,media},session.current,!busy,!!post||!!loaded||(!text&&!media.length));
 const dropDepth=useRef(0);
 const canPublish=!!text.trim()||!!media.length,channels=data.spaces.filter(s=>s.kind==='channel'&&!s.demo);
 const [manageMedia,setManageMedia]=useState(false),[showLocalInfo,setShowLocalInfo]=useState(false),[showLimits,setShowLimits]=useState(false);
 const destination=channels.find(c=>c.id===target),previewAuthor=destination?.name||data.profile;
 useLayoutEffect(()=>{const el=textarea.current;if(!el)return;const top=scroll.current?.scrollTop||0;el.style.height='0px';el.style.height=Math.max(96,el.scrollHeight)+'px';if(scroll.current)scroll.current.scrollTop=top},[text]);
 useComposerCaret(textarea,scroll,text);
 useEffect(()=>{if(!busy)return;const guard=(e:BeforeUnloadEvent)=>{e.preventDefault();e.returnValue=''};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard)},[busy]);
 function close(){if(working.current||closing.current)return;if(changed||post&&loaded)setConfirm(true);else if(draft.pending)void save('draft');else onClose()}
 function add(files:File[]){try{validatePublicationFiles([...media.flatMap(m=>m.file?[m.file]:[]),...files],media.filter(m=>m.attachment).length,media.reduce((n,m)=>n+(m.attachment?.size||0),0));setMedia(m=>[...m,...files.map(file=>({key:recordId(),file,alt:''}))]);setError('')}catch(e){setError(e instanceof Error?e.message:'Не удалось добавить вложения')}}
 function updateText(value:string){setText(value)}
 async function save(mode:'publish'|'draft',overrideRevision?:number){
  if(working.current||closing.current||mode==='publish'&&!canPublish)return;working.current=true;draft.cancelScheduled();setOperation(mode);setBusy(true);setError('');setProgress(0);
  const inputPayload={id:publicationId.current,draftKey,post,text,channelId:target||undefined,media};
  try{
   if(mode==='draft'||(publishScope==='local'&&!isServerPost)){
    const result=await session.current!.save(inputPayload,mode,setProgress);
    if(result){
     closing.current=true;
     toast.success(mode==='draft'?text||media.length?'Черновик сохранён на устройстве':'Пустой черновик удалён':target?'Публикация сохранена на устройстве (в канале и ленте)':'Публикация сохранена на устройстве');
     if(mode==='publish')onPublished?.({...result as FeedPost,syncStatus:'local'});
     onClose();
    }
    return;
   }
   // Server publication or server update flow:
   // Step 1: Always save as a local draft first so network failure or 409 conflict never loses user work!
   const savedDraft=await session.current!.save(inputPayload,'draft',p=>setProgress(Math.round(p*0.4)));
   const attachments=savedDraft?.attachments||[];
   const mediaIds:string[]=[];
   for(const [idx,att] of attachments.entries()){
    const raw=await read<Blob|{bytes:ArrayBuffer;mime:string}>('blobs',att.blobId);
    if(raw){
     const blob=raw instanceof Blob?raw:new Blob([raw.bytes],{type:raw.mime||att.mime});
     await socialApi.uploadMedia(att.id,blob,att.name,att.kind,att.alt||'');
     mediaIds.push(att.id);
    }else if(isServerPost){
     mediaIds.push(att.id);
    }
    setProgress(40+Math.round((idx+1)/(attachments.length+1)*35));
   }
   const extractedTopics=[...new Set((text.match(/#[\p{L}\p{N}_]+/gu)||[]).map(t=>t.slice(1)).slice(0,10))];
   const revToUse=overrideRevision??serverRev;
   const serverRes=isServerPost
    ?await socialApi.act<{post:FeedPost}>({action:'updatePost',id:publicationId.current,revision:revToUse,text:text.trim(),mediaIds,topics:extractedTopics})
    :await socialApi.act<{post:FeedPost}>({action:'createPost',id:publicationId.current,text:text.trim(),channelId:target||undefined,mediaIds,topics:extractedTopics});
   setProgress(100);
   const mergedPost:FeedPost={
    ...serverRes.post,
    attachments:attachments.length?attachments:serverRes.post.attachments,
    blobId:attachments[0]?.blobId||serverRes.post.blobId,
    syncStatus:'server',
    serverRevision:serverRes.post.serverRevision||revToUse+1,
   };
   await commit(s=>discardPostDraft(putFeedPost(s,mergedPost),draftKey));
   closing.current=true;
   setConflictServerPost(null);
   toast.success(isServerPost?`Изменения сохранены на сервере (ревизия #${mergedPost.serverRevision})`:'Публикация размещена на сервере');
   onPublished?.(mergedPost);
   onClose();
  }catch(e){
   setConfirm(false);
   if(e instanceof SocialSyncError&&(e.code==='revision_conflict'||e.status===409)){
    setError('Конфликт ревизий (409): публикация была изменена на другом устройстве. Ваш черновик сохранён локально.');
    try{
     const latest=await socialApi.postDetail(publicationId.current);
     setConflictServerPost(latest.post);
     if(latest.post.serverRevision)setServerRev(latest.post.serverRevision);
    }catch{}
   }else{
    setError((e instanceof Error?e.message:'Ошибка соединения с сервером.')+' Черновик сохранён на устройстве — вы можете повторить отправку.');
   }
  }finally{working.current=false;setBusy(false)}
 }
 async function discard(){if(working.current||closing.current)return;closing.current=true;working.current=true;draft.cancelScheduled();setOperation('discard');setBusy(true);try{await session.current!.discard(draftKey);onClose()}catch{closing.current=false;setError('Не удалось удалить черновик. Попробуйте снова.');setConfirm(false)}finally{working.current=false;setBusy(false)}}
 function picker(ref:React.RefObject<HTMLInputElement|null>){if(!busy&&media.length<10)ref.current?.click()}
 return <>
 <Dialog open onOpenChange={v=>{if(!v)close()}}><DialogContent customPosition showCloseButton={false} className="screen-editor post-composer post-composer-v2" overlayClassName="pc-editor-backdrop" onOpenAutoFocus={e=>{e.preventDefault();textarea.current?.focus({preventScroll:true})}} onInteractOutside={e=>e.preventDefault()} onEscapeKeyDown={e=>{e.preventDefault();close()}}>
  <div className="editor-shell pc-shell"><header className="pc-top"><button aria-label="Закрыть редактор поста" disabled={busy} onClick={close}><X size={22}/></button><DialogTitle>{post?isServerPost?`Редактировать на сервере (рев. #${serverRev})`:'Редактировать пост':'Новая публикация'}</DialogTitle><button className="pc-publish" disabled={busy||!canPublish} onClick={()=>void save('publish')}>{busy?<LoaderCircle size={18} className="animate-spin"/>:<>{post?'Сохранить':publishScope==='server'?'На сервер':'Опубликовать'}<ArrowUpRight size={15}/></>}</button></header>
   <div className="pc-scroll" ref={scroll} data-drop={dropping} onDragEnter={e=>{if(!busy&&e.dataTransfer.types.includes('Files')){e.preventDefault();dropDepth.current++;setDropping(true)}}} onDragOver={e=>{if(!busy&&e.dataTransfer.types.includes('Files')){e.preventDefault();e.dataTransfer.dropEffect='copy'}}} onDragLeave={e=>{if(e.dataTransfer.types.includes('Files')){dropDepth.current=Math.max(0,dropDepth.current-1);if(!dropDepth.current)setDropping(false)}}} onDrop={e=>{if(e.dataTransfer.files.length){e.preventDefault();dropDepth.current=0;setDropping(false);if(!busy)add(Array.from(e.dataTransfer.files))}}}><DialogDescription className="sr-only">Редактор публикации: сохранение на устройстве или синхронизация с сервером Presidium.</DialogDescription>
    <div className="pc-author"><SocialAvatar name={data.profile} blobId={data.avatarId} me/><div className="pc-identity"><b>{data.profile}</b><label className="pc-destination"><select aria-label="Место публикации" disabled={busy||!!post} value={target} onChange={e=>setTarget(e.target.value)}><option value="">Моя лента</option>{channels.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select><ChevronDown size={13}/></label></div><button type="button" className="pc-privacy" disabled={busy||isServerPost} aria-expanded={showLocalInfo} aria-controls="pc-local-info" onClick={()=>{if(!isServerPost)setPublishScope(s=>s==='local'?'server':'local');setShowLocalInfo(true)}}>{publishScope==='server'||isServerPost?<><CloudUpload size={13}/><span>На сервере</span></>:<><Lock size={13}/><span>На устройстве</span></>}</button></div>
    {showLocalInfo&&<p className="pc-local-info" id="pc-local-info">{publishScope==='server'||isServerPost?'Публикация синхронизируется с сервером Presidium (требуется активная подписка Plus или Business). При ошибке сети черновик остаётся на устройстве.':'Пост сохранится только на этом устройстве. Вы сможете явно опубликовать его на сервере позже.'}</p>}
    {error&&<div role="alert" className="pc-error">
     <p>{error}</p>
     {conflictServerPost&&<div className="pc-local-info">
      <b>Актуальная версия на сервере (ревизия #{conflictServerPost.serverRevision||serverRev}):</b>
      <p>{conflictServerPost.text}</p>
      <button type="button" disabled={busy} onClick={()=>void save('publish',conflictServerPost.serverRevision||serverRev)}><RefreshCw size={14}/> Повторить сохранение моего текста поверх ревизии #{conflictServerPost.serverRevision||serverRev}</button>
      <button type="button" disabled={busy} onClick={()=>{setText(conflictServerPost.text);if(conflictServerPost.serverRevision)setServerRev(conflictServerPost.serverRevision);setConflictServerPost(null);setError('')}}>Загрузить серверную версию в редактор</button>
     </div>}
     {!conflictServerPost&&(publishScope==='server'||isServerPost)&&<button type="button" disabled={busy} onClick={()=>void save('publish')}><RefreshCw size={14}/> Повторить отправку на сервер</button>}
    </div>}
    <textarea ref={textarea} rows={3} className="pc-text" aria-label="Текст публикации" placeholder="О чём хотите рассказать?" value={text} maxLength={5000} disabled={busy} onChange={e=>updateText(e.target.value)} onPaste={e=>{const files=Array.from(e.clipboardData.files);if(files.length){if(!e.clipboardData.getData('text/plain'))e.preventDefault();add(files)}}} onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();void save('publish')}}}/>
    {!!media.length&&<PublicationGallery choices={media} busy={busy} suspended={preview||manageMedia||confirm} onManage={()=>setManageMedia(true)} onAdd={()=>picker(photoPicker)}/>}
    {draft.error&&<div className="pc-error pc-draft-error" role="alert"><p>{draft.error}</p><button disabled={busy||draft.status==='saving'} onClick={()=>void draft.flush().catch(()=>{})}>Повторить сохранение</button></div>}
    {dropping&&<p className="pc-drop-hint">Отпустите файлы, чтобы добавить их</p>}
    {busy&&operation!=='discard'&&<div className="pc-progress" role="status"><Progress value={progress}/><span>Сохранение · {progress}%</span></div>}
   </div>
   <footer className="pc-footer">
    <div className="pc-counter"><span className="pc-draft-status" data-status={draft.status} role="status" aria-live="polite">{draft.status==='saving'?<><LoaderCircle size={13} className="animate-spin"/>Сохраняем…</>:draft.status==='error'?<><CloudOff size={13}/>Не сохранено</>:draft.pending?<>Есть изменения</>:(changed||loaded)&&(text||media.length)?<><Check size={13}/>Черновик сохранён</>:<>Сохраняется автоматически</>}</span><span data-limit={text.length>=4500}>{text.length.toLocaleString('ru-RU')}<span> / 5 000</span></span></div>
    {showLimits&&<p className="pc-limits" id="pc-limits">До 10 вложений · до 25 МБ на файл · 100 МБ вместе</p>}
    <div className="pc-toolbar"><button disabled={busy||media.length>=10} onClick={()=>picker(photoPicker)}><ImagePlus size={22} strokeWidth={1.75}/><span>Фото</span></button><button disabled={busy||media.length>=10} onClick={()=>picker(videoPicker)}><Video size={22} strokeWidth={1.75}/><span>Видео</span></button><button disabled={busy||media.length>=10} onClick={()=>picker(documentPicker)}><Paperclip size={22} strokeWidth={1.75}/><span>Файл</span></button><button className="pc-preview-action" aria-label="Предпросмотр публикации" disabled={busy||!canPublish} onClick={()=>setPreview(true)}><Eye size={21} strokeWidth={1.75}/></button><button className="pc-limits-action" aria-label="Ограничения вложений" aria-expanded={showLimits} aria-controls="pc-limits" onClick={()=>setShowLimits(v=>!v)}><Info size={19}/></button></div>
   </footer>
   <input ref={photoPicker} hidden type="file" multiple accept="image/*" onChange={e=>{add(Array.from(e.target.files||[]));e.target.value=''}}/><input ref={videoPicker} hidden type="file" multiple accept="video/*" onChange={e=>{add(Array.from(e.target.files||[]));e.target.value=''}}/><input ref={documentPicker} hidden type="file" multiple accept={POST_DOCUMENT_ACCEPT} onChange={e=>{add(Array.from(e.target.files||[]));e.target.value=''}}/>
  </div>
 </DialogContent></Dialog>
 <PublicationMediaManager open={manageMedia} onClose={()=>setManageMedia(false)} choices={media} onChange={setMedia} busy={busy}/>
 <Dialog open={confirm} onOpenChange={v=>{if(!busy)setConfirm(v)}}><DialogContent customPosition showCloseButton={false} className="pc-exit" overlayClassName="pc-exit-backdrop"><DialogTitle>Оставить черновик?</DialogTitle><DialogDescription>{draft.pending?'Сохраним последние изменения вместе с вложениями на этом устройстве.':'Черновик уже сохранён на устройстве. Можно оставить его или удалить.'}</DialogDescription><button disabled={busy} onClick={()=>void save('draft')}>{busy?operation==='discard'?'Удаляем…':'Сохраняем…':draft.pending?'Сохранить черновик и закрыть':'Оставить черновик'}</button><button className="pc-exit-discard" disabled={busy} onClick={()=>void discard()}>{post?'Отказаться от изменений':'Удалить черновик'}</button><button className="pc-exit-continue" disabled={busy} onClick={()=>setConfirm(false)}>Продолжить редактирование</button></DialogContent></Dialog>
 <Dialog open={preview} onOpenChange={setPreview}><DialogContent customPosition showCloseButton={false} className="screen-editor pc-preview" overlayClassName="pc-preview-backdrop"><div className="editor-shell pc-shell"><header className="pc-top"><button aria-label="Вернуться к редактору" onClick={()=>setPreview(false)}><ChevronLeft size={22}/></button><DialogTitle>Предпросмотр</DialogTitle><button className="pc-preview-done" onClick={()=>setPreview(false)}>Готово</button></header><div className="pc-scroll"><DialogDescription className="sr-only">Предпросмотр текста и выбранных вложений до публикации.</DialogDescription><article className="pc-preview-post"><div className="pc-author"><SocialAvatar name={previewAuthor} blobId={destination?destination.avatarId:data.avatarId} me={!destination}/><div className="pc-identity"><b>{previewAuthor}</b><small>Сейчас · {publishScope==='server'||isServerPost?'На сервере':'На устройстве'}</small></div></div>{text&&<p className="pc-preview-text">{text}</p>}{media.some(c=>choiceKind(c)!=='file')&&<PublicationGallery choices={media.filter(c=>choiceKind(c)!=='file')}/>} {media.filter(c=>choiceKind(c)==='file').map(c=><PublicationMedia key={c.key} choice={c} documentRow/>)}</article><p className="pc-preview-note">Предпросмотр перед публикацией.</p></div></div></DialogContent></Dialog>
 </>;
}
