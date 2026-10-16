import type {FeedPost, PostDraft, PostAttachment} from './domain';
import type {CommitState} from './people';
import {discardPostDraft, publicationWriter, type PublicationInput} from './publication-writer';
import {attachmentsOf} from './feed';
import {referencedMedia} from './backup';
import {pruneMedia} from './storage';

export function publicationSignature(input: PublicationInput): string {
 return JSON.stringify([input.text,input.channelId||'',input.media.map(m=>[
  m.key,m.attachment?.blobId,m.file&&[m.file.name,m.file.type,m.file.size,m.file.lastModified],m.alt,
 ])]);
}

/** Serializes autosave, publish and discard; retains each staged file only once. */
export function publicationSession(commit: CommitState, makeId: ()=>string) {
 const owned=new Set<string>();
 let savingInput:PublicationInput|undefined;
 const writer=publicationWriter(change=>commit(state=>{
  if(savingInput){for(const a of state.postDrafts?.[savingInput.draftKey]?.attachments||[])owned.add(a.blobId);for(const a of attachmentsOf(state.feedPosts?.find(p=>p.id===savingInput!.id)))owned.add(a.blobId)}
  return change(state);
 }),()=>{const id=makeId();owned.add(id);return id});
 const uploaded=new Map<string,{file:File;attachment:PostAttachment}>();
 let queue:Promise<void>=Promise.resolve(),closed=false,publishing=false;
 function enqueue<T>(operation:()=>Promise<T>):Promise<T>{
  const task=queue.then(operation);
  queue=task.then(()=>{},()=>{});
  return task;
 }
 async function prune(){
  // A cleanup failure leaves bytes in place; it cannot undo a committed draft/post.
  try{for(const id of await pruneMedia([...owned],referencedMedia))owned.delete(id)}catch{}
 }
 return {
  save(input:PublicationInput,mode:'draft'|'publish',progress?:(p:number)=>void):Promise<FeedPost|PostDraft|undefined>{
   if(closed||publishing)return Promise.resolve(undefined);
   if(mode==='publish')publishing=true;
   // Input snapshots remain independent while the user keeps typing.
   const snapshot={...input,media:input.media.map(m=>({...m}))};
   const task=enqueue(async()=>{
    if(closed)return;
    savingInput=snapshot;
    const normalized={...snapshot,media:snapshot.media.map(m=>{
     const cached=uploaded.get(m.key);
     return m.file&&cached?.file===m.file?{...m,file:undefined,attachment:cached.attachment}:m;
    })};
    let result:FeedPost|PostDraft|undefined;
    if(mode==='draft'&&!snapshot.post&&!snapshot.text&&!snapshot.media.length){
     await commit(s=>{for(const a of s.postDrafts?.[snapshot.draftKey]?.attachments||[])owned.add(a.blobId);return discardPostDraft(s,snapshot.draftKey)});
     result={id:snapshot.id,text:snapshot.text,channelId:snapshot.channelId,attachments:[],updatedAt:Date.now()};
    }else result=await writer.save(normalized,mode,progress);
    if(result){
     for(const m of snapshot.media){const attachment=result.attachments?.find(a=>a.id===m.key);if(m.file&&attachment)uploaded.set(m.key,{file:m.file,attachment})}
     for(const key of uploaded.keys())if(!snapshot.media.some(m=>m.key===key))uploaded.delete(key);
     await prune();if(mode==='publish')uploaded.clear();
    }
    return result;
   });
   return task.catch(error=>{if(mode==='publish')publishing=false;throw error});
  },
  discard(key:string):Promise<void>{
   // Prevent a queued autosave from resurrecting a discarded draft.
   closed=true;
   return enqueue(async()=>{try{await commit(s=>{for(const a of s.postDrafts?.[key]?.attachments||[])owned.add(a.blobId);return discardPostDraft(s,key)});uploaded.clear();await prune()}catch(error){closed=false;throw error}});
  },
 };
}
export type PublicationSession=ReturnType<typeof publicationSession>;
