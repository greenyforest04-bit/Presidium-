'use client';
import {useEffect,useRef,useState} from 'react';
import type {CSSProperties,PointerEvent,KeyboardEvent} from 'react';
import {Check,Music2,Volume2,VolumeX,ImageOff} from 'lucide-react';
import {read,Story,StoryAsset,StoryLayer} from '@/lib/presidium';

export function useStoryAsset(id?:string,onError?:()=>void){
 const [url,setUrl]=useState('');
 const failure=useRef(onError);failure.current=onError;
 useEffect(()=>{let active=true,url='';setUrl('');if(id)read<Blob>('blobs',id).then(blob=>{if(!active)return;if(blob){url=URL.createObjectURL(blob);setUrl(url);}else failure.current?.();}).catch(()=>{if(active)failure.current?.();});
  return()=>{active=false;if(url)URL.revokeObjectURL(url);};},[id]);return url;
}
function Visual({asset,paused,muted,onReady}:{asset:StoryAsset;paused:boolean;muted:boolean;onReady:()=>void}){
 const [failed,setFailed]=useState(false),url=useStoryAsset(asset.id,()=>{setFailed(true);onReady();}),video=useRef<HTMLVideoElement>(null);
 useEffect(()=>{setFailed(false);},[asset.id]);
 useEffect(()=>{const el=video.current;if(!el)return;if(paused)el.pause();else void el.play().catch(()=>{});},[paused,url]);
 if(failed)return <div className="story-media-error" role="status"><ImageOff/><span>Не удалось открыть медиа</span></div>;
 const style:CSSProperties={transform:asset.mirrored?'scaleX(-1)':undefined};
 const fail=()=>{if(url){setFailed(true);onReady();}};
 return asset.kind==='video'?<video ref={video} src={url||undefined} style={style} playsInline muted={muted} loop preload="auto" onCanPlay={onReady} onError={fail}/>:<img src={url||undefined} style={style} alt="Фото истории" onLoad={onReady} onError={fail}/>;
}
function MusicTrack({music,paused,muted}:{music:NonNullable<Story['music']>;paused:boolean;muted:boolean}){
 const url=useStoryAsset(music.id),ref=useRef<HTMLAudioElement>(null);
 useEffect(()=>{const a=ref.current;if(!a)return;a.currentTime=music.start;},[url,music.start]);
 useEffect(()=>{const a=ref.current;if(!a)return;if(paused||muted)a.pause();else void a.play().catch(()=>{});},[paused,muted,url]);
 return <audio ref={ref} src={url||undefined} preload="metadata" onLoadedMetadata={e=>{e.currentTarget.currentTime=music.start;}} onTimeUpdate={e=>{if(e.currentTarget.currentTime>=music.start+music.length)e.currentTarget.currentTime=music.start;}}/>;
}
function Layer({layer,editable,selected,onSelect,onChange}:{layer:StoryLayer;editable:boolean;selected:boolean;onSelect?:(id:string)=>void;onChange?:(id:string,patch:Partial<StoryLayer>)=>void}){
 const drag=useRef<{x:number;y:number;left:number;top:number;width:number;height:number}|null>(null);
 function down(e:PointerEvent<HTMLDivElement>){if(!editable)return;e.stopPropagation();e.preventDefault();onSelect?.(layer.id);const rect=e.currentTarget.parentElement!.getBoundingClientRect();drag.current={x:e.clientX,y:e.clientY,left:layer.x,top:layer.y,width:rect.width,height:rect.height};e.currentTarget.setPointerCapture(e.pointerId);}
 const clamp=(n:number)=>Math.max(12,Math.min(88,n));
 function move(e:PointerEvent<HTMLDivElement>){const d=drag.current;if(!d)return;e.stopPropagation();onChange?.(layer.id,{x:clamp(d.left+(e.clientX-d.x)/d.width*100),y:clamp(d.top+(e.clientY-d.y)/d.height*100)});}
 function key(e:KeyboardEvent){const delta:{[key:string]:[number,number]}={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};if(delta[e.key]){e.preventDefault();onChange?.(layer.id,{x:clamp(layer.x+delta[e.key][0]),y:clamp(layer.y+delta[e.key][1])});}}
 return <div className={`story-layer ${layer.kind} ${selected?'selected':''} ${layer.plate?'plate':''}`} style={{left:layer.x+'%',top:layer.y+'%',color:layer.color,fontSize:layer.size+'cqw',transform:`translate(-50%,-50%) rotate(${layer.rotation}deg)`}} role={editable?'button':undefined} tabIndex={editable?0:undefined} aria-label={editable?'Слой: '+layer.text:undefined} aria-pressed={editable?selected:undefined} onFocus={()=>editable&&onSelect?.(layer.id)} onKeyDown={key} onPointerDown={down} onPointerMove={move} onPointerUp={e=>{if(drag.current)e.stopPropagation();drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>{layer.text}</div>;
}
export function StoryScene({story,paused=false,editable=false,vote,onVote,selected,onSelectLayer,onLayerChange,onReady}:{story:Story;paused?:boolean;editable?:boolean;vote?:number;onVote?:(n:number)=>void;selected?:string;onSelectLayer?:(id:string)=>void;onLayerChange?:(id:string,patch:Partial<StoryLayer>)=>void;onReady?:()=>void}){
 const [muted,setMuted]=useState(true);
 const media=story.media||(story.blobId?[{id:story.blobId,kind:'image' as const}]:[]);
 const ready=useRef(new Set<string>()),readyCallback=useRef(onReady);readyCallback.current=onReady;
 const mediaKey=media.map(a=>a.id).join(',');
 useEffect(()=>{ready.current.clear();if(!mediaKey&&!story.imageSrc)readyCallback.current?.();},[mediaKey,story.imageSrc]);
 function readyMedia(id:string){ready.current.add(id);if(media.every(a=>ready.current.has(a.id)))readyCallback.current?.();}
 const stop=(e:PointerEvent)=>e.stopPropagation();
 return <div className="studio-scene" style={{background:story.background||'#141418'}}>
  {story.imageSrc&&<img className="editorial-story-background" src={story.imageSrc} alt="Фото истории" onLoad={()=>readyCallback.current?.()} onError={()=>readyCallback.current?.()}/>}
  <div className={`studio-media count-${media.length} layout-${story.layout||'grid'}`}>{media.map(a=><Visual key={a.id} asset={a} paused={paused} muted={muted||!!story.music} onReady={()=>readyMedia(a.id)}/>)}</div>
  {story.text&&!story.layers?.length&&<div className="studio-caption" style={{top:(story.textY??48)+'%',color:story.textColor||'#fff'}}>{story.text}</div>}
  {story.layers?.map(layer=><Layer key={layer.id} layer={layer} editable={editable} selected={selected===layer.id} onSelect={onSelectLayer} onChange={onLayerChange}/>)}
  {story.repost&&<span className="studio-attribution">↗ {story.repost.author}</span>}
  {story.poll&&<div className="studio-poll" onPointerDown={stop} onPointerUp={stop}><strong>{story.poll.question}</strong><div>{story.poll.options.map((option,i)=><button key={i} disabled={editable||!onVote} aria-pressed={vote===i} onClick={()=>onVote?.(i)}>{option}{vote===i&&<Check size={15}/>}</button>)}</div>{vote!==undefined&&<small>Ваш выбор сохранён</small>}</div>}
  {story.music&&<div className="studio-track"><Music2 size={15}/><span>{story.music.name}</span><MusicTrack music={story.music} paused={paused} muted={muted}/></div>}
  {(story.music||media.some(a=>a.kind==='video'))&&<button className="studio-sound" aria-label={muted?'Включить звук истории':'Выключить звук истории'} onPointerDown={stop} onPointerUp={stop} onClick={()=>setMuted(m=>!m)}>{muted?<VolumeX/>:<Volume2/>}</button>}
 </div>;
}
