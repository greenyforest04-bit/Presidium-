'use client';
import {useEffect,useRef,useState,type CSSProperties,type ReactNode,type PointerEvent} from 'react';
import {Mic,Square,Trash2,Send,Lock,ChevronUp,ChevronLeft,LoaderCircle,X,Download} from 'lucide-react';
import {MatrixVoiceRecorder,voiceTime,type VoiceState,type VoiceDraft} from '@/lib/matrix-voice';
import {VoiceGesture} from '@/lib/voice-gesture';
import {VoicePlayer,useVoiceUrl} from './voice-player';

export function VoiceComposer({disabled=false,canSend=true,hidden=false,onSend,onClear,onDirty,onActive,children}:{disabled?:boolean;canSend?:boolean;hidden?:boolean;onSend:(draft:VoiceDraft)=>Promise<void>;onClear?:()=>void;onDirty:(dirty:boolean)=>void;onActive?:(active:boolean)=>void;children?:ReactNode}){
 const [state,setState]=useState<VoiceState>({phase:'idle',elapsed:0}),[mode,setMode]=useState<'held'|'locked'|'preview'>('preview'),[sending,setSending]=useState(false),[error,setError]=useState(''),[hint,setHint]=useState('');
 const surface=useRef<HTMLElement>(null),cancelDistance=useRef(96);
 function setOffset(value:{left:number;up:number}){const element=surface.current;if(!element)return;element.style.setProperty('--voice-left',`${value.left}px`);element.style.setProperty('--voice-up',`${value.up}px`);element.style.setProperty('--voice-cancel',String(Math.min(1,value.left/cancelDistance.current)))}
 const recorder=useRef<MatrixVoiceRecorder|undefined>(undefined),gesture=useRef(new VoiceGesture()),intent=useRef(false),sendingNow=useRef(false),alive=useRef(false),lifetime=useRef(0),suppressClick=useRef(false),callbacks=useRef({onSend,onClear,onDirty,onActive,canSend,disabled}),button=useRef<HTMLButtonElement>(null),url=useVoiceUrl(state.draft?.file);
 callbacks.current={onSend,onClear,onDirty,onActive,canSend,disabled};
 const active=['requesting','recording','stopping'].includes(state.phase),dirty=active||state.phase==='ready';
 useEffect(()=>{
  alive.current=true;lifetime.current++;const capture=new MatrixVoiceRecorder(value=>{if(alive.current&&recorder.current===capture)setState(value)});recorder.current=capture;
  const hidden=()=>{if(!document.hidden)return;intent.current=false;gesture.current.cancel();setMode('preview');if(capture.snapshot().phase==='requesting')capture.cancel();else capture.stop('Запись остановлена при уходе со страницы.')};
  const leaving=()=>{intent.current=false;gesture.current.cancel();capture.cancel();callbacks.current.onClear?.()},escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&capture.snapshot().phase!=='idle'&&!sendingNow.current){event.preventDefault();clear()}};
  document.addEventListener('visibilitychange',hidden);window.addEventListener('pagehide',leaving);document.addEventListener('keydown',escape);
  return()=>{alive.current=false;lifetime.current++;intent.current=false;gesture.current.cancel();capture.cancel();callbacks.current.onClear?.();callbacks.current.onDirty(false);callbacks.current.onActive?.(false);document.removeEventListener('visibilitychange',hidden);window.removeEventListener('pagehide',leaving);document.removeEventListener('keydown',escape)};
 },[]);
 useEffect(()=>{callbacks.current.onDirty(dirty);callbacks.current.onActive?.(active)},[dirty,active]);
 useEffect(()=>{if(state.phase==='ready'){setMode('preview');setOffset({left:0,up:0});if(intent.current){intent.current=false;if(!state.notice)void send(state.draft!)}}if(state.phase==='error'){intent.current=false;gesture.current.cancel();setMode('preview');setOffset({left:0,up:0})}},[state]);
 function clear(){intent.current=false;gesture.current.cancel();recorder.current?.cancel();callbacks.current.onClear?.();setError('');setHint('');setOffset({left:0,up:0});setMode('preview')}
 async function send(draft:VoiceDraft){
  if(sendingNow.current)return;
  if(!callbacks.current.canSend||callbacks.current.disabled){setMode('preview');setHint('Запись сохранена в черновике. Отправьте её, когда соединение восстановится.');return}
  const generation=lifetime.current;sendingNow.current=true;setSending(true);setError('');
  try{await callbacks.current.onSend(draft);if(alive.current&&lifetime.current===generation)clear()}
  catch(problem){if(alive.current&&lifetime.current===generation){setMode('preview');setError(problem instanceof Error?problem.message:'Не удалось отправить запись. Попробуйте снова.')}}
  finally{sendingNow.current=false;if(alive.current&&lifetime.current===generation)setSending(false)}
 }
 function start(next:'held'|'locked'){
  if(callbacks.current.disabled||sendingNow.current||!['idle','error'].includes(recorder.current?.snapshot().phase||'idle'))return false;
  clear();setMode(next);if(document.activeElement instanceof HTMLTextAreaElement||document.activeElement instanceof HTMLInputElement)document.activeElement.blur();document.querySelectorAll('audio,video').forEach(element=>(element as HTMLMediaElement).pause());void recorder.current?.start();
  return true;
 }
 function stop(sendAfter=false){intent.current=sendAfter;setMode('preview');setOffset({left:0,up:0});if(recorder.current?.snapshot().phase==='requesting'){intent.current=false;clear()}else recorder.current?.stop()}
 function down(event:PointerEvent<HTMLButtonElement>){
  if(event.button!==0||dirty||disabled||sending)return;
  event.preventDefault();suppressClick.current=true;cancelDistance.current=Math.min(96,(event.currentTarget.closest('.voice-input-row')?.clientWidth||320)*.3);if(!start('held'))return;gesture.current.begin(event.pointerId,event.clientX,event.clientY,performance.now());event.currentTarget.setPointerCapture(event.pointerId);
 }
 function move(event:PointerEvent<HTMLButtonElement>){
  const result=gesture.current.move(event.pointerId,event.clientX,event.clientY,cancelDistance.current);if(!result)return;
  if(result.action==='cancel'){clear();setHint('Запись отменена');return}
  if(result.action==='lock'){setMode('locked');setOffset({left:0,up:0});if(navigator.vibrate)navigator.vibrate(12);return}
  setOffset({left:result.left,up:result.up});
 }
 function up(event:PointerEvent<HTMLButtonElement>){
  const snapshot=recorder.current?.snapshot(),result=gesture.current.end(event.pointerId,performance.now(),snapshot?.phase||'idle',snapshot?.elapsed||0);
  if(result==='lock'){setMode('locked');setOffset({left:0,up:0})}else if(result==='send')stop(true);else if(result==='cancel'){clear();setHint('Удерживайте микрофон, чтобы записать сообщение.')}
 }
 function cancel(event:PointerEvent<HTMLButtonElement>){if(gesture.current.cancel(event.pointerId)&&['requesting','recording'].includes(recorder.current?.snapshot().phase||'')){clear();setHint('Запись отменена')}}
 const locked=mode==='locked',ready=state.phase==='ready',waiting=state.phase==='requesting'||state.phase==='stopping';
 return <section ref={surface} className={'voice-composer '+(dirty?'is-dirty ':'')+(hidden&&!dirty?'is-hidden':'')} data-mode={mode} data-phase={state.phase} style={{'--voice-level':String((state.level||0)/1024)} as CSSProperties} aria-label="Запись голосового сообщения">
  {dirty&&<div className="voice-record-bar">
   {ready?<><button type="button" className="voice-discard" aria-label="Удалить голосовой черновик" disabled={sending} onClick={clear}><Trash2 size={20}/></button><VoicePlayer src={url} duration={state.draft?.metadata.duration} waveform={state.draft?.metadata.waveform} name={state.draft?.file.name} disabled={sending}/></>:<><button type="button" className={'voice-discard '+(!locked?'held-cancel':'')} aria-label="Отменить запись" disabled={sending} onClick={clear}>{locked?<Trash2 size={20}/>:<X size={20}/>}</button><span className="voice-clock">{waiting?<LoaderCircle size={15} className="matrix-record-loading"/>:<i className="matrix-record-dot"/>}<time>{voiceTime(state.elapsed)}</time></span>{locked?<><div className="voice-live-level" aria-hidden="true"><i/></div><button type="button" className="voice-stop" aria-label="Остановить и прослушать запись" disabled={state.phase!=='recording'} onClick={()=>stop()}><Square size={19} fill="currentColor"/></button></>:<span className="voice-slide-hint"><ChevronLeft size={16}/>{state.phase==='requesting'?'Разрешите микрофон':'Влево — отмена'}</span>}</>}
  </div>}
  {active&&mode==='held'&&<div className="voice-lock-rail" aria-hidden="true"><Lock size={17}/><ChevronUp size={19}/></div>}
  <button ref={button} type="button" className={'voice-mic '+(dirty?'recording':'')} disabled={sending||disabled&&!dirty||ready&&!canSend||state.phase==='stopping'} aria-label={ready?'Отправить голосовое сообщение':locked&&active?'Отправить запись':'Удерживайте для записи. Влево — отмена, вверх — фиксация. Нажмите для записи без удержания.'} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel} onLostPointerCapture={cancel} onContextMenu={event=>event.preventDefault()} onClick={event=>{if(event.detail&&suppressClick.current){suppressClick.current=false;return}if(ready&&state.draft)void send(state.draft);else if(locked&&active)stop(true);else if(!dirty)start('locked')}}>
   {sending||state.phase==='stopping'?<LoaderCircle size={22} className="matrix-record-loading"/>:ready||locked&&active?<Send size={22}/>:<Mic size={23}/>}
  </button>
  {(state.error||error||hint||state.notice)&&<p className="voice-notice" role={state.error||error?'alert':'status'}>{state.error||error||state.notice||hint}{ready&&url&&error&&<a href={url} download={state.draft?.file.name}><Download size={16}/>Скачать запись</a>}</p>}
  {children}
 </section>;
}
