'use client';
import {useEffect,useRef,useState} from 'react';
import type {MatrixTransport,NetworkMessage,TransferProgress} from '@/lib/matrix-transport';
import {attachmentRevision} from '@/lib/matrix-room-content';
import {safeFileName} from '@/lib/matrix-media';
import type {VoiceDraft} from '@/lib/matrix-voice';
import {Transfer} from './matrix-attachments';
import {VoiceComposer} from './voice-composer';
import {VoicePlayer,useVoiceUrl} from './voice-player';
type Runner=(action:()=>Promise<void>)=>Promise<void>;

export function MatrixVoiceComposer({engine,roomId,busy,blocked,online,hidden,run,onDirty,onActive,replyTo,onSent}:{engine:MatrixTransport;roomId:string;busy:boolean;blocked:boolean;online:boolean;hidden?:boolean;run:Runner;onDirty:(value:boolean)=>void;onActive:(value:boolean)=>void;replyTo?:string;onSent:()=>void}){
 const [progress,setProgress]=useState<TransferProgress>(),uploadId=useRef(''),source=useRef<File|undefined>(undefined),controller=useRef<AbortController|undefined>(undefined),alive=useRef(false),lifetime=useRef(0);
 useEffect(()=>{alive.current=true;lifetime.current++;return()=>{alive.current=false;lifetime.current++;controller.current?.abort();engine.forgetUpload(uploadId.current)}},[engine,roomId]);
 function clear(){controller.current?.abort();engine.forgetUpload(uploadId.current);uploadId.current='';source.current=undefined;if(alive.current)setProgress(undefined)}
 async function send(draft:VoiceDraft){
  if(source.current!==draft.file){engine.forgetUpload(uploadId.current);uploadId.current=crypto.randomUUID();source.current=draft.file}
  const id=uploadId.current,generation=lifetime.current,request=new AbortController();controller.current=request;let failure:unknown,performed=false;
  await run(async()=>{
   performed=true;try{await engine.sendVoice(roomId,draft.file,id,draft.metadata,value=>{if(alive.current&&generation===lifetime.current)setProgress(value)},request,replyTo);if(alive.current&&generation===lifetime.current)onSent()}
   catch(problem){if(alive.current&&generation===lifetime.current&&engine.messages(roomId).some(message=>message.failed&&message.uploadId===id)){onSent();return}failure=problem;throw problem}
   finally{if(controller.current===request)controller.current=undefined;if(alive.current&&generation===lifetime.current)setProgress(undefined)}
  });
  if(!performed){if(controller.current===request)controller.current=undefined;throw Error('Отправка не началась. Проверьте подключение к аккаунту и попробуйте снова.')}
  if(failure)throw failure;
 }
 return <VoiceComposer disabled={busy||blocked} canSend={online} hidden={hidden} onSend={send} onClear={clear} onDirty={onDirty} onActive={onActive}><Transfer progress={progress} cancel={()=>controller.current?.abort()}/></VoiceComposer>;
}

export function MatrixVoiceMessage({engine,roomId,message,busy,online,run}:{engine:MatrixTransport;roomId:string;message:NetworkMessage;busy:boolean;online:boolean;run:Runner}){
 const [loaded,setLoaded]=useState<{blob:Blob;revision:string}>(),[progress,setProgress]=useState<TransferProgress>(),[error,setError]=useState(''),revision=attachmentRevision(message),blob=loaded?.revision===revision?loaded?.blob:undefined,url=useVoiceUrl(blob),voice=message.attachment?.voice,controller=useRef<AbortController|undefined>(undefined),lifetime=useRef(0),alive=useRef(false);
 useEffect(()=>{alive.current=true;lifetime.current++;setLoaded(undefined);setProgress(undefined);setError('');return()=>{alive.current=false;lifetime.current++;controller.current?.abort()}},[engine,roomId,revision]);
 async function load(){
  if(!revision||controller.current)return;
  const generation=lifetime.current,request=new AbortController();controller.current=request;setError('');setProgress({stage:'downloading',loaded:0});let failure:unknown,performed=false;
  await run(async()=>{performed=true;try{const value=await engine.downloadAttachment(roomId,message.id,p=>{if(alive.current&&generation===lifetime.current&&!request.signal.aborted)setProgress(p)},request,revision);if(alive.current&&generation===lifetime.current&&!request.signal.aborted)setLoaded({blob:value,revision})}catch(problem){failure=problem;if(alive.current&&generation===lifetime.current&&!request.signal.aborted)setError(problem instanceof Error?problem.message:'Не удалось скачать запись.');throw problem}finally{if(controller.current===request)controller.current=undefined;if(alive.current&&generation===lifetime.current)setProgress(undefined)}});
  if(!performed){if(controller.current===request)controller.current=undefined;if(alive.current&&generation===lifetime.current){setProgress(undefined);setError('Загрузка не началась. Проверьте подключение к аккаунту.')}throw Error('Загрузка не началась.')}
  if(failure)throw failure;
 }
 if(!voice)return null;
 return <section className="matrix-voice-message"><VoicePlayer key={revision} src={url} duration={voice.duration} waveform={voice.waveform} name={safeFileName(message.attachment!.name)} onLoad={load} loading={!!progress} loadProgress={progress?.total?Math.round(progress.loaded/progress.total*100):undefined} disabled={busy||!url&&!revision}/>{progress&&<button type="button" className="voice-cancel-download" onClick={()=>{controller.current?.abort();setProgress(undefined)}}>Отменить загрузку</button>}{error&&<p className="voice-error" role="alert">{error}</p>}</section>;
}
