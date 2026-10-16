'use client';
import {useEffect,useRef,useState,type CSSProperties} from 'react';
import {Play,Pause,Download,LoaderCircle} from 'lucide-react';
import {voiceTime,compressWaveform} from '@/lib/matrix-voice';

export function useVoiceUrl(blob?:Blob){
 const [object,setObject]=useState<{blob:Blob;url:string}>();
 useEffect(()=>{if(!blob){setObject(undefined);return}const url=URL.createObjectURL(blob);setObject({blob,url});return()=>URL.revokeObjectURL(url)},[blob]);
 return object?.blob===blob?object?.url:undefined;
}
export function VoiceWave({samples,progress=0}:{samples:number[];progress?:number}){
 const bars=compressWaveform(samples,40);
 return <div className={'voice-wave '+(!bars.length?'empty':'')} style={{'--voice-bars':bars.length} as CSSProperties} aria-hidden="true">{bars.map((value,index)=><i key={index} style={{height:Math.max(3,value/1024*28)}} data-played={index/bars.length<progress||undefined}/>)}{!bars.length&&<i style={{width:`${progress*100}%`}}/>}</div>;
}
export function VoicePlayer({src,duration=0,waveform=[],name='voice',onLoad,loading=false,loadProgress,disabled=false,onError}:{src?:string;duration?:number;waveform?:number[];name?:string;onLoad?:()=>Promise<void>;loading?:boolean;loadProgress?:number;disabled?:boolean;onError?:(message:string)=>void}){
 const audio=useRef<HTMLAudioElement>(null),currentSrc=useRef(src),intent=useRef(false),mounted=useRef(false);
 currentSrc.current=src;
 const [playing,setPlaying]=useState(false),[position,setPosition]=useState(0),[nativeDuration,setNativeDuration]=useState(0),[speed,setSpeed]=useState(1),[error,setError]=useState('');
 const total=nativeDuration||duration/1000,ratio=total?Math.min(1,position/total):0;
 useEffect(()=>{mounted.current=true;const hidden=()=>{if(document.hidden){intent.current=false;audio.current?.pause()}};document.addEventListener('visibilitychange',hidden);return()=>{mounted.current=false;intent.current=false;audio.current?.pause();document.removeEventListener('visibilitychange',hidden)}},[]);
 useEffect(()=>{setPlaying(false);setPosition(0);setNativeDuration(0);setError('');if(src&&intent.current){intent.current=false;void play()}},[src]);
 async function play(){
  const element=audio.current;if(!element)return;
  setError('');document.querySelectorAll('audio,video').forEach(other=>{if(other!==element)(other as HTMLMediaElement).pause()});element.playbackRate=speed;
  try{await element.play()}catch{if(mounted.current&&currentSrc.current===element.getAttribute('src'))setError('Нажмите ▶, чтобы воспроизвести запись.')}
 }
 async function toggle(){if(disabled||loading)return;if(!src){if(onLoad){intent.current=true;try{await onLoad()}catch{intent.current=false}}return}if(audio.current?.paused)await play();else audio.current?.pause()}
 function seek(value:number){const element=audio.current;if(!element||!total)return;const next=value/1000*total;try{element.currentTime=next;setPosition(next)}catch{setError('Этот файл пока не поддерживает перемотку.')}}
 return <div className="voice-player" role="group" aria-label="Голосовое сообщение">
  <button type="button" className="voice-play" disabled={disabled||loading||!src&&!onLoad} aria-label={loading?'Загрузка голосового':!src?'Скачать и прослушать голосовое':playing?'Приостановить голосовое':'Воспроизвести голосовое'} onClick={()=>void toggle()}>{loading?<LoaderCircle className="matrix-record-loading" size={23}/>:!src?<Download size={22}/>:playing?<Pause size={21} fill="currentColor"/>:<Play size={21} fill="currentColor"/>}</button>
  <div className="voice-track"><div className="voice-seek"><VoiceWave samples={waveform} progress={ratio}/><input type="range" min={0} max={1000} step={1} value={Math.round(ratio*1000)} disabled={!src||!total||disabled} aria-label="Перемотать голосовое сообщение" aria-valuetext={`${voiceTime(position*1000)} из ${voiceTime(total*1000)}`} onChange={event=>seek(Number(event.target.value))}/></div><div className="voice-foot"><time>{loading&&loadProgress!==undefined?`${loadProgress}%`:voiceTime((playing||position>0?position:total)*1000)}</time><span className="voice-length">{position>0?'/ '+voiceTime(total*1000):''}</span>{src&&<a href={src} download={name} aria-label="Сохранить аудиофайл"><Download size={14}/></a>}</div></div>
  <button type="button" className="voice-speed" disabled={!src||disabled} aria-label={`Скорость ${speed}. Изменить скорость`} onClick={()=>{const next=speed===1?1.5:speed===1.5?2:1;setSpeed(next);if(audio.current)audio.current.playbackRate=next}}>{speed}×</button>
  {src&&<audio ref={audio} src={src} preload="metadata" onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>{setPlaying(false);setPosition(0);if(audio.current)audio.current.currentTime=0}} onLoadedMetadata={event=>{const value=event.currentTarget.duration;if(Number.isFinite(value)&&value>0)setNativeDuration(value)}} onTimeUpdate={event=>setPosition(event.currentTarget.currentTime)} onError={()=>{const message='Браузер не воспроизводит этот формат. Сохраните аудиофайл.';setError(message);onError?.(message)}}/>}
  {error&&<p className="voice-error" role="status">{error}</p>}
 </div>;
}
