'use client';
import {useState} from 'react';
import {ArrowLeft,Bookmark,Check} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from './ui/dialog';
import {PostAsset} from './social-ui';
import {State,FeedPost} from '@/lib/presidium';
import type {CommitState} from '@/lib/people';
import {capsuleTags,trashCapsule} from '@/lib/capsule';
import {savePostToCapsule} from '@/lib/feed';
import {toast} from 'sonner';
export function PostSaveSheet({post,data,commit,onClose}:{post:FeedPost;data:State;commit:CommitState;onClose:()=>void}){
 const existing=data.capsule?.find(e=>e.id==='saved-post-'+post.id),[collection,setCollection]=useState(existing?.collection||'Из ленты'),[tags,setTags]=useState(existing?.tags?.join(', ')||''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function save(){if(busy)return;setBusy(true);try{await commit(s=>savePostToCapsule(s,post,collection,capsuleTags(tags)));toast.success('Пост сохранён в «'+(collection.trim()||'Из ленты')+'»');onClose()}catch{setError('Не удалось сохранить. Попробуйте снова.')}finally{setBusy(false)}}
 return <Dialog open onOpenChange={v=>{if(!v&&!busy)onClose()}}><DialogContent customPosition showCloseButton={false} className="screen-editor post-save-sheet"><header className="editor-top"><button className="glass-action" aria-label="Закрыть сохранение" disabled={busy} onClick={onClose}><ArrowLeft/></button><DialogTitle>Сохранить в капсулу</DialogTitle><Bookmark/></header><div className="editor-scroll"><DialogDescription>Текст и вложения сохранятся вместе. Выберите коллекцию или создайте новую.</DialogDescription>{error&&<p className="people-error" role="alert">{error}</p>}<div className="post-save-preview"><b>{post.author}</b><p>{post.text}</p><PostAsset post={post}/></div><label className="pw-field">Коллекция<input list="post-collections" maxLength={40} value={collection} disabled={busy} onChange={e=>setCollection(e.target.value)}/><datalist id="post-collections">{[...new Set(['Из ленты',...(data.capsule||[]).filter(e=>!e.deletedAt).map(e=>e.collection)])].map(c=><option key={c} value={c}/>)}</datalist></label><label className="pw-field">Теги через запятую<input maxLength={400} value={tags} disabled={busy} placeholder="Идеи, посмотреть, путешествия" onChange={e=>setTags(e.target.value)}/></label><button className="primary-btn" disabled={busy} onClick={save}><Check/>{busy?'Сохраняем…':existing?'Обновить сохранённое':'Сохранить'}</button>{(existing||data.feedSaved?.includes(post.id))&&<button className="post-unsave" disabled={busy} onClick={async()=>{setBusy(true);try{await commit(s=>({...trashCapsule(s,'saved-post-'+post.id),feedSaved:s.feedSaved?.filter(id=>id!==post.id)}));toast('Убрано из сохранённого');onClose()}catch{setError('Не удалось сохранить изменение')}finally{setBusy(false)}}}>Убрать из сохранённого</button>}</div></DialogContent></Dialog>;
}
