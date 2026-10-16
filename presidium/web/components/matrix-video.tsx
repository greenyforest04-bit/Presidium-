'use client';
import {useEffect,useRef,useState} from 'react';
import {Video,Camera,SwitchCamera,Square,Trash2,Send,X,RotateCcw,LoaderCircle,Download} from 'lucide-react';
import {Dialog as DialogPrimitive} from 'radix-ui';
import {Dialog,DialogPortal,DialogOverlay,DialogTitle,DialogDescription} from './ui/dialog';
import {MatrixVideoRecorder,type VideoState} from '@/lib/matrix-video';
import {voiceTime} from '@/lib/matrix-voice';
import {attachmentRevision} from '@/lib/matrix-room-content';
import {safeFileName} from '@/lib/matrix-media';
import type {MatrixTransport,NetworkMessage,TransferProgress} from '@/lib/matrix-transport';
import {MatrixAttachmentAction,Transfer} from './matrix-attachments';
type Runner=(action:()=>Promise<void>)=>Promise<void>;
function useVideoUrl(blob?:Blob){const [object,setObject]=useState<{blob:Blob;url:string}>();useEffect(()=>{if(!blob){setObject(undefined);return}const url=URL.createObjectURL(blob);setObject({blob,url});return()=>URL.revokeObjectURL(url)},[blob]);return object?.blob===blob?object?.url:undefined}

export function MatrixVideoComposer({engine,roomId,busy,blocked,online,run,onDirty,onActive,replyTo,onSent}:{engine:MatrixTransport;roomId:string;busy:boolean;blocked:boolean;online:boolean;run:Runner;onDirty:(value:boolean)=>void;onActive:(value:boolean)=>void;replyTo?:string;onSent:()=>void}){
 const [open,setOpen]=useState(false),[state,setState]=useState<VideoState>({phase:'idle',elapsed:0,facing:'user'}),[progress,setProgress]=useState<TransferProgress>(),[error,setError]=useState(''),[cameraError,setCameraError]=useState(''),[ready,setReady]=useState(false),[previewError,setPreviewError]=useState('');
 const session=useRef<MatrixVideoRecorder|undefined>(undefined),alive=useRef(false),lifetime=useRef(0),uploadId=useRef(''),controller=useRef<AbortController|undefined>(undefined),live=useRef<HTMLVideoElement>(null),preview=useRef<HTMLVideoElement>(null),launch=useRef<HTMLButtonElement>(null),close=useRef<HTMLButtonElement>(null),url=useVideoUrl(state.draft?.file);
 useEffect(()=>{
  alive.current=true;lifetime.current++;const capture=new MatrixVideoRecorder(value=>{if(alive.current&&session.current===capture){setState(value);if(value.phase==='ready')uploadId.current=crypto.randomUUID()}});session.current=capture;setState(capture.snapshot());uploadId.current='';
  const hidden=()=>{if(document.hidden){capture.suspend();preview.current?.pause()}},leaving=()=>{capture.cancel();controller.current?.abort();engine.forgetUpload(uploadId.current)};
  document.addEventListener('visibilitychange',hidden);window.addEventListener('pagehide',leaving);
  return()=>{alive.current=false;lifetime.current++;document.removeEventListener('visibilitychange',hidden);window.removeEventListener('pagehide',leaving);capture.cancel();controller.current?.abort();engine.forgetUpload(uploadId.current);onDirty(false);onActive(false)};
 },[engine,roomId,onDirty,onActive]);
 useEffect(()=>{
  const element=live.current,stream=state.stream;setReady(false);if(!element||!stream)return;setCameraError('');
  element.srcObject=stream;void element.play().catch(()=>{if(alive.current&&element.srcObject===stream)setCameraError('Нажмите «Показать камеру», чтобы открыть изображение.')});
  return()=>{element.pause();element.srcObject=null};
 },[state.stream]);
 const active=['requesting','preview','starting','recording','stopping'].includes(state.phase),dirty=active||state.phase==='ready';
 useEffect(()=>{onDirty(dirty);onActive(active)},[dirty,active,onDirty,onActive]);
 function clear(){preview.current?.pause();engine.forgetUpload(uploadId.current);uploadId.current='';session.current?.cancel();setError('');setPreviewError('');setProgress(undefined);setCameraError('');setReady(false)}
 function dismiss(){if(busy)return;clear();setOpen(false)}
 function camera(facing:'user'|'environment'='user'){clear();document.querySelectorAll('audio,video').forEach(element=>(element as HTMLMediaElement).pause());void session.current?.open(facing)}
 async function send(){
  const draft=state.draft,id=uploadId.current;if(!draft||!id)return;const generation=lifetime.current,request=new AbortController();controller.current=request;setError('');preview.current?.pause();
  try{await engine.sendVideo(roomId,draft.file,id,draft.metadata,value=>{if(alive.current&&generation===lifetime.current)setProgress(value)},request,replyTo);if(alive.current&&generation===lifetime.current){clear();setOpen(false);onSent()}}
  catch(problem){if(alive.current&&generation===lifetime.current){setError(problem instanceof Error?problem.message:'Не удалось отправить видео.');if(engine.messages(roomId).some(message=>message.failed&&message.uploadId===id)){clear();setOpen(false);onSent()}}throw problem}
  finally{if(controller.current===request)controller.current=undefined;if(alive.current&&generation===lifetime.current)setProgress(undefined)}
 }
 return <section className="matrix-video-composer"><button type="button" ref={launch} aria-label="Записать видео-сообщение" className="secondary-btn" disabled={busy||blocked} onClick={()=>{setOpen(true);camera()}}><Video size={18}/>Видео</button><Dialog open={open} onOpenChange={value=>{if(!value)dismiss()}}><DialogPortal><DialogOverlay className="matrix-video-overlay"/><DialogPrimitive.Content className="matrix-video-studio" onOpenAutoFocus={event=>{event.preventDefault();close.current?.focus({preventScroll:true})}} onCloseAutoFocus={event=>{event.preventDefault();launch.current?.focus({preventScroll:true})}} onPointerDownOutside={event=>event.preventDefault()} onEscapeKeyDown={event=>{event.preventDefault();dismiss()}}>
  <header className="matrix-video-top"><button ref={close} className="glass-action" disabled={busy} aria-label="Отменить видео и закрыть камеру" onClick={dismiss}><X/></button><div><DialogTitle>Видео-сообщение</DialogTitle><DialogDescription>{state.phase==='ready'?'Просмотрите перед отправкой':'Камера и микрофон · до 1 минуты'}</DialogDescription></div>{state.phase==='preview'&&<button className="glass-action" aria-label="Переключить камеру" onClick={()=>camera(state.facing==='user'?'environment':'user')}><SwitchCamera/></button>}</header>
  <div className="matrix-video-stage">
   {state.stream?<video key="live" ref={live} muted autoPlay playsInline className={'matrix-video-live '+(state.facing==='user'?'front':'')} aria-label="Изображение с камеры" onPlaying={event=>{if(event.currentTarget.srcObject===state.stream){setReady(true);setCameraError('')}}} onResize={event=>session.current?.checkFrame(event.currentTarget.videoWidth,event.currentTarget.videoHeight)} onError={event=>{if(!state.stream||event.currentTarget.srcObject!==state.stream)return;session.current?.cancel();setCameraError('Изображение с камеры недоступно. Попробуйте открыть камеру снова.')}}/>:state.phase==='ready'&&url?<video key="preview" ref={preview} src={url} controls playsInline preload="metadata" aria-label="Просмотр видео перед отправкой" onPlay={event=>document.querySelectorAll('audio,video').forEach(other=>{if(other!==event.currentTarget)(other as HTMLMediaElement).pause()})} onError={()=>setPreviewError('Видео не воспроизводится. Запишите сообщение заново.')}/>:<div className="matrix-video-empty">{['requesting','starting','stopping'].includes(state.phase)?<LoaderCircle className="matrix-record-loading" size={36}/>:<Camera size={40}/>}<b>{state.phase==='requesting'?'Разрешите камеру и микрофон':state.phase==='stopping'?'Завершаем запись…':'Камера выключена'}</b></div>}
   {state.phase==='recording'&&<div className="matrix-video-record-badge" role="status"><span className="matrix-record-dot"/><time>{voiceTime(state.elapsed)} / 1:00</time></div>}
  </div>
  <footer className="matrix-video-bottom">
   {(state.error||error||cameraError||previewError)&&<p className="people-error" role="alert">{state.error||error||cameraError||previewError}</p>}
   {state.phase==='preview'&&cameraError&&<button className="secondary-btn" onClick={()=>void live.current?.play().catch(()=>setCameraError('Не удалось показать камеру. Откройте её снова.'))}>Показать камеру</button>}
   {state.phase==='ready'&&<p>{state.notice||`Запись ${voiceTime(state.elapsed)} готова.`}{!online?' Отправка станет доступна после подключения.':''}</p>}
   <div className="matrix-video-controls">{state.phase==='preview'?<button className="matrix-video-shutter" aria-label="Начать запись видео" disabled={!ready||busy} onClick={()=>{const element=live.current;if(element)session.current?.record(element.videoWidth,element.videoHeight,engine.mediaMode)}}><span/></button>:state.phase==='recording'?<button className="matrix-video-shutter recording" aria-label="Остановить запись видео" onClick={()=>session.current?.stop()}><Square fill="currentColor"/></button>:state.phase==='ready'?<><button className="secondary-btn" disabled={busy} onClick={()=>camera()}><RotateCcw size={18}/>Перезаписать</button><button className="primary-btn" disabled={busy||blocked||!online||!!previewError} onClick={()=>void run(send)}><Send size={18}/>Отправить видео</button></>:['idle','error'].includes(state.phase)?<button className="primary-btn" onClick={()=>camera()}><Camera size={18}/>Открыть камеру снова</button>:null}</div>
   {dirty&&<button className="matrix-inline-action matrix-danger-action" disabled={busy} onClick={dismiss}><Trash2 size={18}/>Отменить видео</button>}
   <Transfer progress={progress} cancel={()=>controller.current?.abort()}/>
  </footer>
 </DialogPrimitive.Content></DialogPortal></Dialog></section>
}

export function MatrixVideoMessage({engine,roomId,message,busy,online,run}:{engine:MatrixTransport;roomId:string;message:NetworkMessage;busy:boolean;online:boolean;run:Runner}){
 const [loaded,setLoaded]=useState<{blob:Blob;revision:string}>(),[error,setError]=useState(''),video=useRef<HTMLVideoElement>(null),revision=attachmentRevision(message),blob=loaded?.revision===revision?loaded?.blob:undefined,url=useVideoUrl(blob),metadata=message.attachment?.video;
 useEffect(()=>{setLoaded(undefined);setError('')},[revision]);
 useEffect(()=>{const hidden=()=>{if(document.hidden)video.current?.pause()};document.addEventListener('visibilitychange',hidden);return()=>document.removeEventListener('visibilitychange',hidden)},[]);
 if(!metadata)return null;
 return <section className="matrix-video-message" aria-label="Видео-сообщение"><div className="matrix-record-heading"><Video size={18}/><b>Видео-сообщение</b><time>{voiceTime(metadata.duration)}</time></div>{url?<><video ref={video} src={url} controls playsInline preload="metadata" style={{aspectRatio:`${metadata.width} / ${metadata.height}`}} aria-label="Просмотреть видео-сообщение" onPlay={event=>document.querySelectorAll('audio,video').forEach(other=>{if(other!==event.currentTarget)(other as HTMLMediaElement).pause()})} onError={()=>setError('Видео не воспроизводится в браузере. Сохраните файл для другого проигрывателя.')}/><a className="matrix-inline-action" href={url} download={safeFileName(message.attachment!.name)}><Download size={16}/>Сохранить видео</a></>:<MatrixAttachmentAction engine={engine} roomId={roomId} message={message} busy={busy||!online} run={run} onOpen={(value,_name,current)=>{const selected=attachmentRevision(current);if(selected)setLoaded({blob:value,revision:selected})}}/>}{error&&<p className="people-error" role="alert">{error}</p>}</section>
}
