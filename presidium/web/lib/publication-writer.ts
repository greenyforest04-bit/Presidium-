import type {FeedPost,PostAttachment,PostDraft,State} from './domain';
import {putFeedPost,publicationKind,validatePublicationFiles} from './feed';
import {read,write,removeBlob} from './storage';
import type {CommitState} from './people';

export type PublicationChoice={key:string;attachment?:PostAttachment;file?:File;alt:string};
export type PublicationInput={id:string;draftKey:string;post?:FeedPost;text:string;channelId?:string;media:PublicationChoice[]};
/** One writer per editor: synchronously guards double taps and retries with stable IDs. */
export function publicationWriter(commit:CommitState,makeId:()=>string){
 let working=false,published=false;
 return {async save(input:PublicationInput,mode:'publish'|'draft',progress:(p:number)=>void=()=>{}):Promise<FeedPost|PostDraft|undefined>{
  if(working||published)return;working=true;const fresh:string[]=[];
  const report=(value:number)=>{try{progress(value)}catch{/* UI observers cannot roll back a durable write. */}};
  try{
   if(input.text.length>5000)throw Error('Текст публикации: максимум 5000 символов.');
   const bytes=(await Promise.all(input.media.filter(m=>m.attachment).map(async m=>{
    const blob=await read<Blob>('blobs',m.attachment!.blobId);if(!blob)throw Error('Одно из вложений недоступно. Удалите его или добавьте файл снова.');if(blob.size>25*1024*1024)throw Error('Размер вложения превышает 25 МБ.');return blob.size;
   }))).reduce((a,b)=>a+b,0);
   validatePublicationFiles(input.media.flatMap(m=>m.file?[m.file]:[]),input.media.filter(m=>m.attachment).length,bytes);
   const attachments:PostAttachment[]=[];
   for(const [index,item] of input.media.entries()){
    if(item.attachment)attachments.push({...item.attachment,alt:item.alt.trim()});
    else if(item.file){const id=makeId();fresh.push(id);const mime=item.file.type||'application/octet-stream';await write('blobs',id,{format:'presidium-bytes-v1',bytes:await item.file.arrayBuffer(),mime});attachments.push({id:item.key,blobId:id,kind:publicationKind(item.file),name:item.file.name,mime,size:item.file.size,alt:item.alt.trim()});}
    report(Math.round((index+1)/(input.media.length+1)*100));
   }
   const now=Date.now();
   if(mode==='draft'){
    const draft:PostDraft={id:input.id,text:input.text,channelId:input.channelId,attachments,updatedAt:now};
    await commit(s=>({...s,postDrafts:{...s.postDrafts,[input.draftKey]:draft},drafts:input.draftKey==='new'?{...s.drafts,'feed-composer':input.text}:s.drafts}));report(100);return draft;
   }
   const first=attachments[0],post:FeedPost={...input.post,id:input.id,author:'',text:input.text.trim(),createdAt:input.post?.createdAt??now,updatedAt:input.post?now:undefined,channelId:input.channelId,attachments,blobId:first?.blobId,video:attachments.some(a=>a.kind==='video'),images:undefined};
   let persisted=post;
   await commit(s=>{if(s.feedPosts?.some(p=>p.id===post.id)&&!input.post)throw Error('Эта публикация уже сохранена.');const next=putFeedPost(s,post);persisted=next.feedPosts!.find(p=>p.id===post.id)!;return next});published=true;report(100);return persisted;
  }catch(error){await Promise.allSettled(fresh.map(removeBlob));throw error}finally{working=false}
 }};
}
export function discardPostDraft(state:State,key:string):State{const postDrafts={...state.postDrafts};delete postDrafts[key];return {...state,postDrafts,drafts:key==='new'?{...state.drafts,'feed-composer':''}:state.drafts}}
