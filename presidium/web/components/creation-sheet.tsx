'use client';
import {useRef} from 'react';
import {X,Newspaper,Camera,Radio,Users,NotebookPen,ChevronRight,FilePenLine,Tag} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from './ui/dialog';
export type CreationAction='post'|'story'|'channel'|'room'|'note'|'sell';
export function CreationSheet({open,onClose,onChoose,draft=false,marketplace=false}:{open:boolean;onClose:()=>void;onChoose:(action:CreationAction)=>void;draft?:boolean;marketplace?:boolean}){
 const close=useRef<HTMLButtonElement>(null),sheet=useRef<HTMLDivElement>(null),touch=useRef<{id:number;y:number;at:number;base:number}|null>(null);
 function finishDrag(y?:number){const current=touch.current;touch.current=null;if(!current)return;const amount=y===undefined?0:Math.max(0,y-current.y),velocity=amount/Math.max(1,performance.now()-current.at);sheet.current?.removeAttribute('data-dragging');if(amount>70||amount>28&&velocity>.45){onClose();return}sheet.current?.style.removeProperty('--sheet-drag')}
 const spaces=[{id:'channel',icon:Radio,title:'Канал',description:'Собрать публикации в одном месте'},{id:'room',icon:Users,title:'Комната',description:'Общение с выбранными контактами'},{id:'note',icon:NotebookPen,title:'Заметка',description:'Записать идею в Капсулу'}] as const;
 return <Dialog open={open} onOpenChange={v=>{if(!v)onClose()}}><DialogContent ref={sheet} customPosition showCloseButton={false} className="creation-sheet" overlayClassName="creation-backdrop" onOpenAutoFocus={e=>{e.preventDefault();close.current?.focus()}} onCloseAutoFocus={e=>e.preventDefault()}>
  <div className="creation-grab" onPointerDown={e=>{const el=sheet.current;if(!el)return;const base=Math.max(0,el.getBoundingClientRect().bottom-parseFloat(getComputedStyle(el).top));touch.current={id:e.pointerId,y:e.clientY,at:performance.now(),base};el.style.setProperty('--sheet-drag',base+'px');el.setAttribute('data-dragging','true');el.setAttribute('data-settled','true');e.currentTarget.setPointerCapture(e.pointerId)}} onPointerMove={e=>{if(touch.current?.id===e.pointerId)sheet.current?.style.setProperty('--sheet-drag',Math.max(0,touch.current.base+e.clientY-touch.current.y)+'px')}} onPointerUp={e=>{if(touch.current?.id===e.pointerId)finishDrag(e.clientY)}} onPointerCancel={()=>finishDrag()}><span/></div>
  <header><div><DialogTitle>Создать</DialogTitle><DialogDescription>Момент, мысль или новое пространство</DialogDescription></div><button ref={close} aria-label="Закрыть меню создания" onClick={onClose}><X size={20}/></button></header>
  {marketplace&&<button className="creation-market-action" onClick={()=>onChoose('sell')}><span><Tag size={24} strokeWidth={1.6}/></span><div><b>Продать товар</b><small>Объявление в Комиссионке</small></div><ChevronRight size={18}/></button>}
  <div className="creation-formats">
   <button className="creation-format creation-format-post" onClick={()=>onChoose('post')}><span className="creation-format-icon"><Newspaper size={27} strokeWidth={1.65}/></span><b>Публикация</b><small>Текст, фото и видео</small><ChevronRight size={18}/></button>
   <button className="creation-format creation-format-story" onClick={()=>onChoose('story')}><span className="creation-format-icon"><Camera size={27} strokeWidth={1.65}/></span><b>История</b><small>Моменты на 24 часа</small><ChevronRight size={18}/></button>
  </div>
  {draft&&<button className="creation-resume" onClick={()=>onChoose('post')}><FilePenLine size={18}/><span>Продолжить черновик</span><ChevronRight size={17}/></button>}
  <div className="creation-options">{spaces.map(a=><button key={a.id} onClick={()=>onChoose(a.id)}><span className="creation-icon"><a.icon size={22} strokeWidth={1.7}/></span><span className="creation-copy"><b>{a.title}</b><small>{a.description}</small></span><ChevronRight size={17}/></button>)}</div>
  <p className="creation-local">Комнаты используют Matrix. Редактор каналов пока не подключён; серверные каналы доступны в аккаунте.</p>
 </DialogContent></Dialog>;
}
