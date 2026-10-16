'use client';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowUp,ArrowDown,ChevronLeft,ChevronRight,Download,FileText,ImagePlus,SlidersHorizontal,Trash2,X} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from './ui/dialog';
import {read} from '@/lib/presidium';
import {publicationKind} from '@/lib/feed';
import {type PublicationChoice} from '@/lib/publication-writer';

export const choiceKind=(choice:PublicationChoice)=>choice.file?publicationKind(choice.file):choice.attachment!.kind;
export const choiceName=(choice:PublicationChoice)=>choice.file?.name||choice.attachment?.name||'Вложение';

/** Object URLs belong to this mounted view, never to persisted draft metadata. */
export function PublicationMedia({choice,active=true,thumbnail=false,documentRow=false}:{choice:PublicationChoice;active?:boolean;thumbnail?:boolean;documentRow?:boolean}){
 const [url,setUrl]=useState(''),[failed,setFailed]=useState(false),[size,setSize]=useState(choice.file?.size??choice.attachment?.size),video=useRef<HTMLVideoElement>(null);
 const kind=choiceKind(choice),name=choiceName(choice);
 useEffect(()=>{
  let live=true,u='';setUrl('');setFailed(false);
  const apply=(blob?:Blob)=>{if(!live)return;if(blob){u=URL.createObjectURL(blob);setUrl(u);setSize(blob.size)}else setFailed(true)};
  if(choice.file)apply(choice.file);else read<Blob>('blobs',choice.attachment!.blobId).then(apply).catch(()=>{if(live)setFailed(true)});
  return()=>{live=false;if(u)URL.revokeObjectURL(u)};
 },[choice.file,choice.attachment?.blobId]);
 useEffect(()=>{if(!active||thumbnail)video.current?.pause()},[active,thumbnail,url]);
 if(failed)return <span className="pc-media-state" role="status"><FileText size={25}/><span>Не удалось открыть файл.<br/>Добавьте его повторно.</span></span>;
 const sizeLabel=size===undefined?'Документ':size>=1048576?(size/1048576).toFixed(1)+' МБ':Math.max(1,Math.ceil(size/1024))+' КБ';
 if(kind==='file'&&documentRow)return <div className="post-document"><FileText size={24}/><span><b>{name}</b><small>{sizeLabel}</small></span>{url&&<a href={url} download={name} aria-label={'Скачать '+name}><Download size={20}/></a>}</div>;
 if(kind==='file')return <div className="pc-file-preview"><FileText size={thumbnail?22:38} strokeWidth={1.5}/>{!thumbnail&&<><b>{name}</b><small>{sizeLabel}</small>{url&&<a href={url} download={name} aria-label={'Скачать '+name}><Download size={16}/>Скачать файл</a>}</>}</div>;
 if(!url)return <span className="pc-media-state" role="status">Открываем…</span>;
 return kind==='video'?<video ref={video} src={url} controls={!thumbnail&&active} tabIndex={active&&!thumbnail?0:-1} playsInline preload="metadata" onError={()=>setFailed(true)}/>:<img src={url} alt={choice.alt||'Фото публикации'} onError={()=>setFailed(true)}/>;
}

export function PublicationGallery({choices,busy=false,suspended=false,onManage,onAdd}:{choices:PublicationChoice[];busy?:boolean;suspended?:boolean;onManage?:()=>void;onAdd?:()=>void}){
 const [index,setIndex]=useState(0),rail=useRef<HTMLDivElement>(null),selected=useRef(choices[0]?.key),cursor=useRef(0),frame=useRef(0);
 const order=choices.map(c=>c.key).join('|');
 useLayoutEffect(()=>{
  cancelAnimationFrame(frame.current);
  const found=choices.findIndex(c=>c.key===selected.current),next=found>=0?found:Math.min(cursor.current,Math.max(0,choices.length-1));
  setIndex(next);cursor.current=next;selected.current=choices[next]?.key;
  if(rail.current)rail.current.scrollLeft=rail.current.clientWidth*next;
  // Only restore the visible item when order changes, not when its caption changes.
 },[order]);
 useEffect(()=>{const el=rail.current;if(!el||typeof ResizeObserver==='undefined')return;const observer=new ResizeObserver(()=>{el.scrollLeft=el.clientWidth*cursor.current});observer.observe(el);return()=>observer.disconnect()},[]);
 useEffect(()=>()=>cancelAnimationFrame(frame.current),[]);
 function go(next:number){
  const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches||document.documentElement.dataset.reduceMotion==='true';
  rail.current?.scrollTo({left:rail.current.clientWidth*next,behavior:reduce?'instant':'smooth'});
 }
 const current=choices[index]||choices[0];
 if(!current)return null;
 return <section className="pc-gallery" aria-label="Вложения публикации">
  <div className="pc-gallery-frame"><div className="pc-media-rail" ref={rail} onScroll={()=>{
   cancelAnimationFrame(frame.current);frame.current=requestAnimationFrame(()=>{const el=rail.current;if(!el)return;const next=Math.max(0,Math.min(choices.length-1,Math.round(el.scrollLeft/Math.max(1,el.clientWidth))));selected.current=choices[next]?.key;cursor.current=next;setIndex(old=>old===next?old:next)});
  }}>{choices.map((choice,i)=><div className="pc-media-slide" key={choice.key} inert={i!==index} aria-label={'Вложение '+(i+1)+' из '+choices.length}><PublicationMedia choice={choice} active={i===index&&!suspended&&!busy}/></div>)}</div>
   <span className="pc-gallery-count" aria-live="polite">{index+1}<span> / {choices.length}</span></span>
   {choices.length>1&&<><button className="pc-gallery-prev" disabled={index===0} aria-label="Предыдущее вложение" onClick={()=>go(index-1)}><ChevronLeft size={21}/></button><button className="pc-gallery-next" disabled={index===choices.length-1} aria-label="Следующее вложение" onClick={()=>go(index+1)}><ChevronRight size={21}/></button></>}
  </div>
  <div className="pc-gallery-caption"><span title={choiceName(current)}>{choiceName(current)}</span>{onManage&&<button disabled={busy} onClick={onManage}><SlidersHorizontal size={16}/>Изменить</button>}</div>
  <div className="pc-gallery-bottom">{choices.length>1&&<div className="pc-gallery-dots" aria-label="Выбор вложения">{choices.map((c,i)=><button key={c.key} aria-label={'Показать вложение '+(i+1)} aria-current={index===i?'true':undefined} onClick={()=>go(i)}><span/></button>)}</div>}{onAdd&&choices.length<10&&<button className="pc-gallery-add" disabled={busy} onClick={onAdd}><ImagePlus size={16}/>Добавить фото</button>}</div>
 </section>;
}

export function PublicationMediaManager({open,onClose,choices,onChange,busy}:{open:boolean;onClose:()=>void;choices:PublicationChoice[];onChange:React.Dispatch<React.SetStateAction<PublicationChoice[]>>;busy:boolean}){
 const close=useRef<HTMLButtonElement>(null);
 useEffect(()=>{if(open&&!choices.length)onClose()},[open,choices.length,onClose]);
 function move(index:number,delta:number){onChange(items=>{const next=[...items];[next[index],next[index+delta]]=[next[index+delta],next[index]];return next})}
 return <Dialog open={open} onOpenChange={v=>{if(!v)onClose()}}><DialogContent customPosition showCloseButton={false} className="pc-media-manager" overlayClassName="pc-manager-backdrop" onOpenAutoFocus={e=>{e.preventDefault();close.current?.focus()}}>
  <header><div><DialogTitle>Вложения</DialogTitle><DialogDescription>Порядок, описания и удаление</DialogDescription></div><button ref={close} aria-label="Готово: вернуться к публикации" onClick={onClose}><X size={20}/></button></header>
  <div className="pc-manager-list">{choices.map((choice,index)=><article key={choice.key}>
   <div className="pc-manager-file"><div className="pc-manager-thumb"><PublicationMedia choice={choice} thumbnail/></div><span><b>{choiceName(choice)}</b><small>{choiceKind(choice)==='image'?'Фото':choiceKind(choice)==='video'?'Видео':'Документ'} · {index+1} из {choices.length}</small></span><button disabled={busy} aria-label={'Удалить '+choiceName(choice)} onClick={()=>onChange(items=>items.filter(m=>m.key!==choice.key))}><Trash2 size={19}/></button></div>
   <div className="pc-manager-order"><span>Порядок</span><button disabled={busy||index===0} aria-label={'Переместить '+choiceName(choice)+' раньше'} onClick={()=>move(index,-1)}><ArrowUp size={17}/></button><button disabled={busy||index===choices.length-1} aria-label={'Переместить '+choiceName(choice)+' позже'} onClick={()=>move(index,1)}><ArrowDown size={17}/></button></div>
   {choiceKind(choice)==='image'&&<details className="pc-alt"><summary>{choice.alt?'Изменить описание фото':'Добавить описание фото'}</summary><label><span>Для людей, использующих экранный диктор</span><input aria-label={'Описание '+choiceName(choice)} maxLength={300} disabled={busy} value={choice.alt} placeholder="Что изображено на фото?" onChange={e=>onChange(items=>items.map(m=>m.key===choice.key?{...m,alt:e.target.value}:m))}/></label></details>}
  </article>)}</div>
  <button className="pc-manager-done" onClick={onClose}>Готово</button>
 </DialogContent></Dialog>;
}
