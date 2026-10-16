'use client';
import {forwardRef,useEffect,useImperativeHandle,useMemo,useRef,useState} from 'react';
import {ArrowLeft,ImageIcon,FileIcon,Link,Search,Film,Music,Mic,MessageSquare,Copy,ExternalLink} from 'lucide-react';
import {toast} from 'sonner';
import {Dialog as DialogPrimitive} from 'radix-ui';
import type {MatrixTransport,NetworkMessage} from '@/lib/matrix-transport';
import {attachmentRevision,roomContent,filterRoomContent,type RoomContentKind} from '@/lib/matrix-room-content';
import {Dialog,DialogPortal,DialogOverlay,DialogTitle,DialogDescription} from './ui/dialog';
import {Tabs,TabsList,TabsTrigger,TabsContent} from './ui/tabs';
import {voiceTime} from '@/lib/matrix-voice';
import {MatrixAttachmentAction,MatrixMediaPreview} from './matrix-attachments';
type Runner=(action:()=>Promise<void>)=>Promise<void>;
const tabs=[{id:'media',name:'Фото и видео',icon:ImageIcon},{id:'files',name:'Файлы',icon:FileIcon},{id:'links',name:'Ссылки',icon:Link}] as const;
function dateLabel(time:number){return Number.isFinite(time)?new Date(time).toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric'}):'Дата неизвестна'}
function sizeLabel(size:number){return Number.isFinite(size)&&size>0?(size<1024*1024?`${Math.ceil(size/1024)} КБ`:`${(size/1024/1024).toFixed(1)} МБ`):'Размер неизвестен'}

export const MatrixRoomContent=forwardRef<{back:()=>void},{open:boolean;onClose:()=>void;onSource:(id:string)=>void;engine:MatrixTransport;roomId:string;messages:NetworkMessage[];busy:boolean;online:boolean;run:Runner;onHistory:()=>Promise<void>;returnFocus:()=>void}>(function MatrixRoomContent({open,onClose,onSource,engine,roomId,messages,busy,online,run,onHistory,returnFocus},ref){
 const [tab,setTab]=useState<RoomContentKind>('media'),[query,setQuery]=useState(''),[preview,setPreview]=useState<{blob:Blob;name:string;id:string;revision:string}>(),[error,setError]=useState('');
 const pendingSource=useRef(''),libraryScroll=useRef<HTMLDivElement>(null),backButton=useRef<HTMLButtonElement>(null);
 const items=useMemo(()=>roomContent(messages),[messages]),filtered=useMemo(()=>filterRoomContent(items,tab,query),[items,tab,query]);
 const previewValid=preview&&attachmentRevision(messages.find(message=>message.id===preview.id))===preview.revision;
 useEffect(()=>{if(!open){setPreview(undefined);setError('');setQuery('')}},[open]);
 useEffect(()=>{if(preview&&!previewValid){setPreview(undefined);setError('Вложение изменилось или удалено. Выберите другое.')}},[preview,previewValid]);
 useImperativeHandle(ref,()=>({back:()=>{if(preview)setPreview(undefined);else onClose()}}));
 function source(id:string){pendingSource.current=id;onClose()}
 async function earlier(){setError('');const scroller=libraryScroll.current,offset=scroller?.scrollTop||0;try{await onHistory();requestAnimationFrame(()=>{if(scroller?.isConnected)scroller.scrollTop=offset})}catch(problem){setError(problem instanceof Error?problem.message:'Не удалось загрузить историю.');throw problem}}
 return <Dialog open={open} onOpenChange={value=>{if(!value){if(preview)setPreview(undefined);else onClose()}}}><DialogPortal><DialogOverlay className="matrix-library-overlay"/><DialogPrimitive.Content className="matrix-library-dialog" onOpenAutoFocus={event=>{event.preventDefault();backButton.current?.focus({preventScroll:true})}} onCloseAutoFocus={event=>{event.preventDefault();const id=pendingSource.current;pendingSource.current='';if(id)onSource(id);else returnFocus()}} onEscapeKeyDown={event=>{if(preview){event.preventDefault();setPreview(undefined)}}}>
  <div inert={!!previewValid} aria-hidden={previewValid?true:undefined} className="matrix-library-shell">
   <header className="matrix-library-top"><button ref={backButton} className="glass-action" aria-label="Вернуться в чат" onClick={onClose}><ArrowLeft/></button><div><DialogTitle>Материалы чата</DialogTitle><DialogDescription>В загруженной истории</DialogDescription></div><span>{items.length}</span></header>
   <Tabs value={tab} onValueChange={value=>{setTab(value as RoomContentKind);libraryScroll.current?.scrollTo({top:0})}} className="matrix-library-tabs">
    <div className="matrix-library-controls"><label className="matrix-library-search"><Search size={18}/><input type="search" aria-label="Поиск материалов чата" placeholder="Название, текст или автор" value={query} maxLength={150} onChange={event=>setQuery(event.target.value)}/></label><TabsList aria-label="Тип материалов">{tabs.map(({id,name,icon:Icon})=><TabsTrigger key={id} value={id}><Icon size={17}/><span>{name}</span><b>{items.filter(item=>item.kind===id).length}</b></TabsTrigger>)}</TabsList><div className="matrix-library-summary"><span role="status" aria-live="polite">{query.trim()?`Найдено: ${filtered.length}`:'Сначала новые'}</span><button className="matrix-inline-action" disabled={busy||!online} onClick={()=>void run(earlier)}>Загрузить раньше</button></div></div>
    <div ref={libraryScroll} className="matrix-library-scroll">
     {error&&<p className="people-error" role="alert">{error}</p>}
     {tabs.map(({id,name,icon:EmptyIcon})=><TabsContent key={id} value={id}>
      {!filtered.length?<div className="matrix-library-empty"><EmptyIcon size={36}/><b>{query.trim()?'Ничего не найдено':`${name}: пока пусто`}</b><p>{query.trim()?'Попробуйте другое название, текст или имя автора.':'Материалы появятся, когда соответствующие сообщения загрузятся и расшифруются.'}</p></div>:<div className={'matrix-library-items '+(id==='media'?'matrix-library-grid':'')}>{filtered.map(item=>{
       const message=item.message,attachment=message.attachment,Icon=id==='links'?Link:attachment?.voice?Mic:attachment?.mime.startsWith('video/')?Film:attachment?.mime.startsWith('image/')?ImageIcon:attachment?.mime.startsWith('audio/')?Music:FileIcon;
       return <article className="matrix-library-item" key={item.key}><div className="matrix-library-symbol"><Icon size={id==='media'?36:24}/>{id==='media'&&<span>{attachment?.mime.startsWith('video/')?'Видео':'Фото'}</span>}</div><div className="matrix-library-copy"><b>{attachment?.voice?'Голосовое сообщение · '+voiceTime(attachment.voice.duration):attachment?.video?'Видео-сообщение · '+voiceTime(attachment.video.duration):item.host||item.title}</b>{item.href&&<p>{item.href}</p>}<small>{dateLabel(message.time)}{attachment?' · '+sizeLabel(attachment.size):''}</small><small>{message.sender}{message.edited?' · изменено':''}</small></div><div className="matrix-library-actions">
        {attachment?<MatrixAttachmentAction engine={engine} roomId={roomId} message={message} busy={busy||!online} run={run} onOpen={(blob,name,current)=>{const revision=attachmentRevision(current);if(revision)setPreview({blob,name,id:current.id,revision})}}/>:<><a className="secondary-btn" href={item.href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"><ExternalLink size={16}/>Открыть сайт</a><button className="glass-action" aria-label="Скопировать ссылку" onClick={()=>void navigator.clipboard.writeText(item.href!).then(()=>toast.success('Ссылка скопирована')).catch(()=>toast.error('Не удалось скопировать ссылку.'))}><Copy size={17}/></button></>}
        <button className="matrix-inline-action" onClick={()=>source(message.id)}><MessageSquare size={16}/>К сообщению</button>
       </div></article>
      })}</div>}
     </TabsContent>)}
     <p className="matrix-library-note">Вложения загружаются по нажатию. Для старых материалов загрузите более ранние сообщения.{tab==='links'?' Сайт откроется в новой вкладке.':''}</p>
    </div>
   </Tabs>
  </div>
  {preview&&previewValid&&<MatrixMediaPreview blob={preview.blob} name={preview.name} onClose={()=>setPreview(undefined)} onSource={()=>source(preview.id)}/>}
 </DialogPrimitive.Content></DialogPortal></Dialog>
});
