'use client';
import {useEffect,useRef,useState} from 'react';
import {UserRound,MoreHorizontal,Check,ChevronRight,X,UserPlus,FileText,Download} from 'lucide-react';
import {DropdownMenu,DropdownMenuTrigger,DropdownMenuContent,DropdownMenuItem,DropdownMenuSeparator} from '@/components/ui/dropdown-menu';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/components/ui/dialog';
import {Tabs,TabsList,TabsTrigger} from '@/components/ui/tabs';
import {Portrait} from './presidium-editors';
import {State,FeedPost,read} from '@/lib/presidium';
import {attachmentsOf} from '@/lib/feed';
import {getFeedPosts} from '@/lib/social-content';

export function SocialAvatar({name,src,blobId,me=false}:{name:string;src?:string;blobId?:string;me?:boolean}){
  if(src)return <span className="social-avatar"><img src={src} alt=""/></span>;
  if(me&&!blobId)return <span className="social-avatar social-avatar-me"><UserRound/></span>;
  return <Portrait name={name} id={blobId}/>;
}

export function SegmentControl({value,onChange,items,className=''}:{value:string;onChange:(value:string)=>void;items:[string,string][];className?:string}){
  return <Tabs value={value} onValueChange={onChange} className={'social-segments '+className}><TabsList>{items.map(([id,label])=><TabsTrigger key={id} value={id}>{label}</TabsTrigger>)}</TabsList></Tabs>;
}

export function SocialMenu({label='Действия',icon,items}:{label?:string;icon?:React.ReactNode;items:{label:string;action:()=>void;icon?:React.ReactNode;selected?:boolean;danger?:boolean;separator?:boolean}[]}){
 return <DropdownMenu><DropdownMenuTrigger asChild><button className="social-menu-trigger" aria-label={label}>{icon||<MoreHorizontal/>}</button></DropdownMenuTrigger><DropdownMenuContent align="end" sideOffset={8} collisionPadding={12} className="social-popup">{items.map((item,i)=><div key={item.label}>{item.separator&&i>0&&<DropdownMenuSeparator/>}<DropdownMenuItem onSelect={item.action} className={item.danger?'social-danger':''}>{item.icon}<span>{item.label}</span>{item.selected&&<Check size={16}/>}</DropdownMenuItem></div>)}</DropdownMenuContent></DropdownMenu>;
}

function PostFile({blobId,name,size}:{blobId:string;name:string;size?:number}){
 const [url,setUrl]=useState(''),[failed,setFailed]=useState(false),[retry,setRetry]=useState(0);
 useEffect(()=>{let live=true,u='';setUrl('');setFailed(false);read<Blob>('blobs',blobId).then(b=>{if(!live)return;if(!b){setFailed(true);return}u=URL.createObjectURL(b);setUrl(u)}).catch(()=>{if(live)setFailed(true)});return()=>{live=false;if(u)URL.revokeObjectURL(u)}},[blobId,retry]);
 return <div className="post-document"><FileText size={24}/><span><b>{name}</b><small>{size?size>=1048576?(size/1048576).toFixed(1)+' МБ':Math.ceil(size/1024)+' КБ':'Документ'}{!url&&!failed?' · открываем…':''}</small></span>{failed?<button onClick={()=>setRetry(v=>v+1)}>Повторить</button>:url&&<a href={url} download={name} aria-label={'Скачать '+name}><Download size={20}/></a>}</div>;
}
function PostSlide({src,blobId,video,full,active,alt,onOpen}:{src?:string;blobId?:string;video:boolean;full:boolean;active:boolean;alt:string;onOpen?:()=>void}){
 const [url,setUrl]=useState(src||''),[failed,setFailed]=useState(false),[attempt,setAttempt]=useState(0);const ref=useRef<HTMLVideoElement>(null);
 useEffect(()=>{let live=true,u='';setFailed(false);setUrl(src||'');if(blobId)read<Blob>('blobs',blobId).then(blob=>{if(!live)return;if(blob){u=URL.createObjectURL(blob);setUrl(u)}else setFailed(true)}).catch(()=>{if(live)setFailed(true)});return()=>{live=false;if(u)URL.revokeObjectURL(u)}},[src,blobId,attempt]);
 useEffect(()=>{if(!active)ref.current?.pause()},[active]);
 if(failed)return <div className="social-media-loading"><span>Не удалось открыть вложение</span><button onClick={()=>setAttempt(v=>v+1)}>Повторить</button></div>;
 if(!url)return <div className="social-media-loading" aria-busy="true">Открываем медиа…</div>;
 if(video)return <video ref={ref} src={url} controls playsInline preload="metadata" autoPlay={full&&active} onError={()=>setFailed(true)}/>;
 const img=<img src={url} alt={alt} loading={active?'eager':'lazy'} onError={()=>setFailed(true)}/>;
 return onOpen?<button onClick={onOpen} aria-label="Открыть фото на весь экран">{img}</button>:img;
}
export function PostAsset({post,full=false,onOpen}:{post:FeedPost;full?:boolean;onOpen?:()=>void}){
 const [index,setIndex]=useState(0);
 const attachments=attachmentsOf(post);
 const items=attachments.length?attachments.filter(a=>a.kind!=='file').map(a=>({id:a.id,blobId:a.blobId,src:undefined as string|undefined,video:a.kind==='video',alt:a.alt||'Фото публикации '+post.author})):post.images?.map((src,i)=>({id:src+i,src,blobId:undefined as string|undefined,video:false,alt:'Фото публикации '+post.author}))||[];
 const files=attachments.filter(a=>a.kind==='file');
 useEffect(()=>setIndex(0),[post.id]);
 if(!items.length&&!files.length)return null;
 return <>{!!items.length&&<div className={'post-gallery '+(full?'is-full':'')}><div className="post-gallery-scroll" onScroll={e=>{const el=e.currentTarget;setIndex(Math.round(el.scrollLeft/Math.max(1,el.clientWidth)))}}>{items.map((item,i)=><div className="post-slide" key={item.id}><PostSlide {...item} full={full} active={i===index} onOpen={onOpen}/></div>)}</div>{items.length>1&&<><span className="post-gallery-counter">{index+1}/{items.length}</span><div className="post-gallery-dots" aria-label="Вложения публикации">{items.map((_,i)=><button key={i} aria-label={'Вложение '+(i+1)} aria-current={i===index} onClick={e=>{const el=e.currentTarget.closest('.post-gallery')!.querySelector('.post-gallery-scroll')!;el.scrollTo({left:el.clientWidth*i,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})}}/>)}</div></>}</div>}{files.map(a=><PostFile key={a.id} {...a}/>)}</>;
}

export function AuthorSheet({author,data,setData,onClose,onPost,onSpace}:{author:string|null;data:State;setData:React.Dispatch<React.SetStateAction<State>>;onClose:()=>void;onPost:(text:string)=>void;onSpace?:(id:string)=>void}){
 const posts=getFeedPosts(data).filter(p=>p.author===author),first=posts[0],space=data.spaces.find(s=>s.name===author),following=!!author&&!!data.feedFollowing?.includes(author);
 return <Dialog open={!!author} onOpenChange={v=>{if(!v)onClose()}}><DialogContent customPosition showCloseButton={false} className="screen-editor author-sheet"><header className="editor-top"><button className="glass-action" aria-label="Закрыть профиль автора" onClick={onClose}><X/></button><span>Профиль</span><span/></header><div className="editor-scroll"><div className="author-cover" style={{backgroundImage:first?.images?.[0]?`url(${first.images[0]})`:undefined}}/><div className="author-identity"><SocialAvatar name={author||''} src={first?.avatarSrc} blobId={space?.avatarId}/><DialogTitle>{author}</DialogTitle><DialogDescription>{space?.description||first?.location||'Публикации и истории'}</DialogDescription>{first?.demo&&<small>Демонстрационный профиль</small>}</div><div className="author-buttons"><button className={following?'secondary-btn':'primary-btn'} onClick={()=>setData(d=>({...d,feedFollowing:following?d.feedFollowing?.filter(a=>a!==author):[...(d.feedFollowing||[]),author!]}))}>{following?<Check/>:<UserPlus/>}{following?'Вы подписаны':'Подписаться'}</button>{space&&onSpace&&<button className="secondary-btn" onClick={()=>{onClose();onSpace(space.id)}}>Открыть канал<ChevronRight/></button>}</div><h2 className="author-post-title">Публикации <span>{posts.length}</span></h2><div className="author-post-grid">{posts.map(p=><button key={p.id} onClick={()=>{onClose();onPost(p.text)}}>{p.images?.[0]?<img src={p.images[0]} alt=""/>:<span>{p.text}</span>}</button>)}</div></div></DialogContent></Dialog>;
}
