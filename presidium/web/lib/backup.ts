import {z} from 'zod';
import type {State,Story,FeedPost} from './domain';
import type {LocalSnapshot,StoredMedia,StoryDraft} from './storage';

export const BACKUP_MAX_BYTES=128*1024*1024;
const META_MAX_BYTES=10*1024*1024;
const magic=new TextEncoder().encode('PRESIDIUM-BACKUP/1\n');
const HEADER_BYTES=magic.length+16+12;
const id=z.string().min(1).max(200),text=z.string().max(1000000),time=z.number().finite().nonnegative();
const ids=z.array(id).max(100000),optionalIds=ids.optional();
const attachment=z.object({id,blobId:id,kind:z.enum(['image','video','file']),name:text,mime:text,size:time.optional(),alt:text.optional()});
const post=z.object({id,author:text,text,createdAt:time,blobId:id.optional(),video:z.boolean().optional(),demo:z.boolean().optional(),images:z.array(text).max(100).optional(),avatarSrc:text.optional(),location:text.optional(),topics:z.array(text).optional(),attachments:z.array(attachment).max(10).optional(),channelId:id.optional(),updatedAt:time.optional()});
const story=z.object({id,name:text,text,color:text,createdAt:time,blobId:id.optional(),demo:z.boolean().optional(),imageSrc:text.optional(),layers:z.array(z.object({id,kind:z.enum(['text','sticker']),text,x:z.number().finite(),y:z.number().finite(),size:z.number().finite(),rotation:z.number().finite(),color:text,plate:z.boolean().optional()})).max(500).optional(),layout:z.enum(['grid','rows','columns']).optional(),media:z.array(z.object({id,kind:z.enum(['image','video']),mirrored:z.boolean().optional(),duration:time.optional()})).max(100).optional(),background:text.optional(),textY:z.number().finite().optional(),textColor:text.optional(),duration:time.optional(),poll:z.object({question:text,options:z.array(text).max(100)}).optional(),music:z.object({id,name:text,start:time,length:time,duration:time.optional()}).optional(),repost:z.object({id,author:text}).optional()});
const space=z.object({id,name:text,initials:text,color:text,kind:z.enum(['chat','group','channel','saved']),description:text,avatarId:id.optional(),commentsEnabled:z.boolean().optional(),reactionsEnabled:z.boolean().optional(),discussionId:id.optional(),signature:z.boolean().optional(),members:optionalIds,archived:z.boolean().optional(),unread:time.optional(),pinned:z.boolean().optional(),demo:z.boolean().optional()});
const message=z.object({id,spaceId:id,text,sender:text,own:z.boolean(),createdAt:time,kind:z.enum(['text','file','audio','video']),blobId:id.optional(),fileName:text.optional(),mime:text.optional(),voice:z.object({duration:z.number().int().min(1).max(24*60*60*1000),waveform:z.array(z.number().int().min(0).max(1024)).max(120)}).optional(),demo:z.boolean().optional(),replyTo:text.optional(),edited:z.boolean().optional(),starred:z.boolean().optional()});
const contact=z.object({id,name:text,phone:text,handle:text,bio:text,favorite:z.boolean().optional(),avatarId:id.optional(),spaceId:id.optional()});
const notebookBlock=z.object({id,kind:z.enum(['text','heading','task','quote','divider']),text:z.string().max(10000),checked:z.boolean().optional()});
const entry=z.object({blocks:z.array(notebookBlock).max(200).optional(),icon:z.string().max(20).optional(),meeting:z.object({startsAt:z.string().datetime({offset:true}),participants:z.array(id).max(50),roomId:id.optional()}).optional(),id,title:text,text,collection:text,kind:z.enum(['note','task','file']),post:post.optional(),story:story.optional(),tags:z.array(text).optional(),updatedAt:time.optional(),deletedAt:time.optional(),done:z.boolean().optional(),pinned:z.boolean().optional(),blobId:id.optional(),fileName:text.optional(),createdAt:time});
const safeKey=z.string().refine(v=>!['__proto__','constructor','prototype'].includes(v));
const publicationDraft=z.object({id,text:z.string().max(5000),channelId:id.optional(),attachments:z.array(attachment).max(10),updatedAt:time});
const stateSchema=z.object({postDrafts:z.record(safeKey,publicationDraft).optional(),version:z.literal(1),profile:text,compact:z.boolean(),spaces:z.array(space).max(100000),messages:z.array(message).max(100000),stories:z.array(story).max(100000),drafts:z.record(safeKey,text),contacts:z.array(contact).max(100000).optional(),capsule:z.array(entry).max(100000).optional(),feedPosts:z.array(post).max(100000).optional(),bio:text.optional(),handle:text.optional(),avatarId:id.optional(),phone:text.optional(),wallpaper:z.enum(['soviet','plain']).optional(),reduceMotion:z.boolean().optional(),enterToSend:z.boolean().optional(),textSize:z.number().min(10).max(40).optional(),notificationRead:optionalIds,notificationHidden:optionalIds,searchHistory:z.array(text).optional(),feedHidden:optionalIds,feedFollowing:z.array(text).optional(),feedLikes:optionalIds,feedSaved:optionalIds,seenStories:optionalIds,likedStories:optionalIds,storyVotes:z.record(safeKey,z.number().finite()).optional(),feedComments:z.record(safeKey,z.array(text)).optional()});
const draftSchema=z.object({story,owned:ids});
const manifestSchema=z.object({format:z.literal('presidium'),version:z.literal(1),createdAt:time,state:stateSchema,storyDraft:draftSchema.optional(),missing:ids,media:z.array(z.object({id,size:time,mime:text,sha256:z.string().regex(/^[a-f0-9]{64}$/)})).max(100000)});
export type PreparedBackup=LocalSnapshot&{createdAt:number;missing:string[];legacy:boolean};
export type RestoreOptions={profile:boolean;preferences:boolean};
export type BackupProgress=(value:number,label:string)=>void;
function validateState(raw:unknown):State{
 const result=stateSchema.safeParse(raw);if(!result.success)throw Error('Файл содержит несовместимые или повреждённые записи.');
 const state=result.data;for(const records of [state.spaces,state.messages,state.stories,state.contacts||[],state.capsule||[],state.feedPosts||[]])if(new Set(records.map(r=>r.id)).size!==records.length)throw Error('В копии найдены повторяющиеся идентификаторы.');
 return state as State;
}
export function referencedMedia(state:State,draft?:StoryDraft){
 const refs=new Set<string>();const add=(id?:string)=>{if(id)refs.add(id)};
 const storyRefs=(s:Story)=>{add(s.blobId);s.media?.forEach(a=>add(a.id));add(s.music?.id)};
 const postRefs=(p:FeedPost)=>{add(p.blobId);p.attachments?.forEach(a=>add(a.blobId))};
 add(state.avatarId);state.spaces.forEach(s=>add(s.avatarId));state.contacts?.forEach(c=>add(c.avatarId));state.messages.forEach(m=>add(m.blobId));state.stories.forEach(storyRefs);state.feedPosts?.forEach(postRefs);Object.values(state.postDrafts||{}).forEach(d=>d.attachments.forEach(a=>add(a.blobId)));state.capsule?.forEach(e=>{add(e.blobId);if(e.story)storyRefs(e.story);if(e.post)postRefs(e.post)});if(draft)storyRefs(draft.story);
 return [...refs];
}
async function keyFor(password:string,salt:Uint8Array<ArrayBuffer>){
 if(!crypto.subtle)throw Error('Защищённая копия недоступна в этом браузере. Откройте Presidium по HTTPS.');
 const material=await crypto.subtle.importKey('raw',new TextEncoder().encode(password),'PBKDF2',false,['deriveKey']);
 return crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:310000,hash:'SHA-256'},material,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);
}
async function hash(bytes:ArrayBuffer){const digest=await crypto.subtle.digest('SHA-256',bytes);return Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('')}
export async function encodeBackup(snapshot:LocalSnapshot,password:string,onProgress:BackupProgress=()=>{}):Promise<{file:Blob;missing:string[]}>{
 if(password.length<10)throw Error('Используйте пароль из 10 или более символов.');
 const state=validateState(snapshot.state),storyDraft=snapshot.storyDraft?draftSchema.parse(snapshot.storyDraft) as StoryDraft:undefined;
 const refs=referencedMedia(state,storyDraft),available=new Map(snapshot.blobs.map(m=>[m.id,m.blob]));const missing=refs.filter(id=>!available.has(id));
 const selected=refs.flatMap(id=>available.has(id)?[{id,blob:available.get(id)!}]:[]);
 if(selected.reduce((size,m)=>size+m.blob.size,0)>BACKUP_MAX_BYTES-META_MAX_BYTES)throw Error('Медиа занимают больше 118 МБ. Для этой версии уменьшите объём копии или экспортируйте текст отдельно.');
 const media: {id:string;size:number;mime:string;sha256:string}[]=[],parts:BlobPart[]=[];
 for(const [i,m] of selected.entries()){const bytes=await m.blob.arrayBuffer();media.push({id:m.id,size:m.blob.size,mime:m.blob.type,sha256:await hash(bytes)});parts.push(bytes);onProgress(Math.round((i+1)/Math.max(1,selected.length)*60),'Подготовка вложений')}
 const meta=new TextEncoder().encode(JSON.stringify({format:'presidium',version:1,createdAt:Date.now(),state,storyDraft,missing,media}));if(meta.byteLength>META_MAX_BYTES)throw Error('Текст копии занимает больше 10 МБ.');
 const length=new Uint8Array(4);new DataView(length.buffer).setUint32(0,meta.length);
 const payload=new Blob([length,meta,...parts]);if(payload.size+HEADER_BYTES+16>BACKUP_MAX_BYTES)throw Error('Копия превышает 128 МБ.');const plain=await payload.arrayBuffer();
 const header=new Uint8Array(HEADER_BYTES);header.set(magic);crypto.getRandomValues(header.subarray(magic.length,magic.length+16));crypto.getRandomValues(header.subarray(magic.length+16));
 onProgress(70,'Защита копии паролем');const key=await keyFor(password,header.slice(magic.length,magic.length+16));
 const cipher=await crypto.subtle.encrypt({name:'AES-GCM',iv:header.slice(magic.length+16),additionalData:header,tagLength:128},key,plain);onProgress(100,'Копия готова');
 return {file:new Blob([header,cipher],{type:'application/octet-stream'}),missing};
}
export async function decodeBackup(file:Blob,password:string,onProgress:BackupProgress=()=>{}):Promise<PreparedBackup>{
 if(!file.size||file.size>BACKUP_MAX_BYTES)throw Error('Выберите резервную копию размером до 128 МБ.');
 const prefix=new Uint8Array(await file.slice(0,magic.length).arrayBuffer());
 if(!magic.every((v,i)=>prefix[i]===v)){
  if(file.size>META_MAX_BYTES)throw Error('Неизвестный формат копии.');
  let raw:unknown;try{raw=JSON.parse(await file.text())}catch{throw Error('Выберите файл .presidium или прежний экспорт JSON.')}
  const state=validateState(raw);return {state,blobs:[],missing:referencedMedia(state),createdAt:Date.now(),legacy:true};
 }
 if(file.size<HEADER_BYTES+20)throw Error('Файл копии обрезан или повреждён.');
 onProgress(10,'Открытие копии');const header=new Uint8Array(await file.slice(0,HEADER_BYTES).arrayBuffer());
 const key=await keyFor(password,header.slice(magic.length,magic.length+16));let plain:ArrayBuffer;
 try{plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:header.slice(magic.length+16),additionalData:header,tagLength:128},key,await file.slice(HEADER_BYTES).arrayBuffer())}catch{throw Error('Неверный пароль или повреждённая копия. Текущие данные не изменены.')}
 if(plain.byteLength<4)throw Error('Копия повреждена.');const length=new DataView(plain).getUint32(0);if(length>META_MAX_BYTES||length+4>plain.byteLength)throw Error('Описание копии повреждено.');
 let raw:unknown;try{raw=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(plain.slice(4,4+length)))}catch{throw Error('Описание копии повреждено.')}
 const parsed=manifestSchema.safeParse(raw);if(!parsed.success)throw Error('Формат копии не поддерживается.');const manifest=parsed.data,state=validateState(manifest.state);
 if(new Set(manifest.media.map(m=>m.id)).size!==manifest.media.length)throw Error('Вложения копии содержат дубликаты.');
 let offset=4+length;const blobs:StoredMedia[]=[];
 for(const [i,m] of manifest.media.entries()){
  if(!Number.isSafeInteger(m.size)||offset+m.size>plain.byteLength)throw Error('Размер вложения не совпадает с копией.');
  const bytes=plain.slice(offset,offset+m.size);if(await hash(bytes)!==m.sha256)throw Error('Одно из вложений повреждено.');
  blobs.push({id:m.id,blob:new Blob([bytes],{type:m.mime})});offset+=m.size;onProgress(40+Math.round((i+1)/Math.max(1,manifest.media.length)*60),'Проверка вложений');
 }
 if(offset!==plain.byteLength)throw Error('Файл содержит лишние данные.');
 const stored=new Set(blobs.map(m=>m.id)),missing=referencedMedia(state,manifest.storyDraft as StoryDraft|undefined).filter(id=>!stored.has(id));
 return {state,blobs,storyDraft:manifest.storyDraft as StoryDraft|undefined,createdAt:manifest.createdAt,missing,legacy:false};
}
function joinRecords<T extends {id:string}>(current:T[]|undefined,incoming:T[]|undefined):T[]|undefined{
 if(current===undefined&&incoming===undefined)return undefined;const ids=new Set(current?.map(r=>r.id));return [...(current||[]),...(incoming||[]).filter(r=>!ids.has(r.id))];
}
export function restoreSummary(current:State,incoming:State){
 const count=(a:{id:string}[]|undefined,b:{id:string}[]|undefined)=>{const ids=new Set(a?.map(r=>r.id));return (b||[]).filter(r=>!ids.has(r.id)).length};
 return {spaces:count(current.spaces,incoming.spaces),messages:count(current.messages,incoming.messages),contacts:count(current.contacts,incoming.contacts),stories:count(current.stories,incoming.stories),posts:count(current.feedPosts,incoming.feedPosts),capsule:count(current.capsule,incoming.capsule)};
}
export function mergeBackup(current:State,backup:PreparedBackup,options:RestoreOptions,hasDraft=false):LocalSnapshot{
 const mapping=new Map(referencedMedia(backup.state,backup.storyDraft).map(id=>[id,'import-'+Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('')]));const map=(id?:string)=>id?mapping.get(id)||id:undefined;
 const mapStory=(s:Story):Story=>({...s,blobId:map(s.blobId),media:s.media?.map(a=>({...a,id:map(a.id)!})),music:s.music?{...s.music,id:map(s.music.id)!}:undefined});
 const mapPost=(p:FeedPost):FeedPost=>({...p,blobId:map(p.blobId),attachments:p.attachments?.map(a=>({...a,blobId:map(a.blobId)!}))});
 const b=backup.state;
 const state:State={...current,spaces:joinRecords(current.spaces,b.spaces.map(s=>({...s,avatarId:map(s.avatarId)})))!,messages:joinRecords(current.messages,b.messages.map(m=>({...m,blobId:map(m.blobId)})))!,stories:joinRecords(current.stories,b.stories.map(mapStory))!,contacts:joinRecords(current.contacts,b.contacts?.map(c=>({...c,avatarId:map(c.avatarId)}))),capsule:joinRecords(current.capsule,b.capsule?.map(e=>({...e,blobId:map(e.blobId),story:e.story?mapStory(e.story):undefined,post:e.post?mapPost(e.post):undefined}))),feedPosts:joinRecords(current.feedPosts,b.feedPosts?.map(mapPost)),postDrafts:{...Object.fromEntries(Object.entries(b.postDrafts||{}).map(([key,d])=>[key,{...d,attachments:d.attachments.map(a=>({...a,blobId:map(a.blobId)!}))}])),...current.postDrafts},drafts:{...b.drafts,...current.drafts},feedComments:{...b.feedComments,...current.feedComments},storyVotes:{...b.storyVotes,...current.storyVotes}};
 for(const key of ['feedHidden','feedFollowing','feedLikes','feedSaved','seenStories','likedStories','notificationRead','notificationHidden','searchHistory'] as const){if(current[key]||b[key])state[key]=[...new Set([...(current[key]||[]),...(b[key]||[])])]}
 if(options.profile){state.profile=b.profile;state.bio=b.bio;state.handle=b.handle;state.phone=b.phone;state.avatarId=map(b.avatarId)}
 if(options.preferences){state.compact=b.compact;state.wallpaper=b.wallpaper;state.reduceMotion=b.reduceMotion;state.enterToSend=b.enterToSend;state.textSize=b.textSize}
 const storyDraft=!hasDraft&&backup.storyDraft?{story:mapStory(backup.storyDraft.story),owned:backup.storyDraft.owned.map(id=>map(id)!)}:undefined;
 const refs=new Set(referencedMedia(state,storyDraft));const blobs=backup.blobs.flatMap(m=>{const id=mapping.get(m.id);return id&&refs.has(id)?[{id,blob:m.blob}]:[]});
 return {state,blobs,storyDraft};
}
