'use client';
import {useEffect,useRef,useState} from 'react';
import type {PostDraft} from '@/lib/domain';
import type {PublicationInput} from '@/lib/publication-writer';
import {publicationSignature,type PublicationSession} from '@/lib/publication-session';

type DraftStatus='saved'|'pending'|'saving'|'error';
export function usePublicationDraft(input:PublicationInput,session:PublicationSession,enabled:boolean,initiallySaved:boolean){
 const signature=publicationSignature(input);
 const [savedKey,setSavedKey]=useState(initiallySaved?signature:''),[status,setStatus]=useState<DraftStatus>(initiallySaved?'saved':'pending'),[error,setError]=useState(''),[inFlight,setInFlight]=useState(false);
 const latest=useRef(input),saved=useRef(savedKey),active=useRef(enabled),alive=useRef(true),timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),failed=useRef(''),jobs=useRef(new Map<string,Promise<PostDraft|undefined>>());
 latest.current=input;active.current=enabled;
 function cancelScheduled(){clearTimeout(timer.current);timer.current=undefined}
 function flush():Promise<PostDraft|undefined>{
  cancelScheduled();if(!active.current)return Promise.resolve(undefined);
  const snapshot=latest.current,key=publicationSignature(snapshot);
  const existing=jobs.current.get(key);if(existing)return existing;
  if(saved.current===key&&!jobs.current.size)return Promise.resolve(undefined);
  failed.current='';if(alive.current){setStatus('saving');setError('');setInFlight(true)}
  const task=session.save(snapshot,'draft').then(result=>{
   if(!result)return;
   saved.current=key;
   if(alive.current){setSavedKey(key);setStatus(publicationSignature(latest.current)===key?'saved':'pending')}
   return result as PostDraft;
  }).catch(problem=>{
   if(publicationSignature(latest.current)===key){failed.current=key;if(alive.current){setStatus('error');setError(problem instanceof Error?problem.message:'Не удалось сохранить черновик. Попробуйте снова.')}}
   throw problem;
  }).finally(()=>{jobs.current.delete(key);if(alive.current)setInFlight(jobs.current.size>0)});
  jobs.current.set(key,task);return task;
 }
 const flushRef=useRef(flush);flushRef.current=flush;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;cancelScheduled()}},[]);
 useEffect(()=>{
  cancelScheduled();if(!enabled)return;
  if(signature===saved.current){if(!jobs.current.size)setStatus('saved');return}
  if(failed.current===signature)return;
  if(!jobs.current.has(signature)){setStatus('pending');timer.current=setTimeout(()=>{void flushRef.current().catch(()=>{})},450)}
  return cancelScheduled;
 },[signature,savedKey,enabled]);
 useEffect(()=>{
  const leaving=(event:BeforeUnloadEvent)=>{if(active.current&&(publicationSignature(latest.current)!==saved.current||jobs.current.size)){event.preventDefault();event.returnValue=''}};
  const hiding=()=>{if(document.visibilityState==='hidden'&&active.current)void flushRef.current().catch(()=>{})};
  window.addEventListener('beforeunload',leaving);document.addEventListener('visibilitychange',hiding);
  return()=>{window.removeEventListener('beforeunload',leaving);document.removeEventListener('visibilitychange',hiding)};
 },[]);
 return {status,error,pending:signature!==savedKey||inFlight,flush,cancelScheduled};
}
