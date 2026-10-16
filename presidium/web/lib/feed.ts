import type {State,FeedPost,PostAttachment} from './presidium';
export const POST_MEDIA_LIMIT=10;
export const POST_FILE_LIMIT=25*1024*1024;
export const POST_TOTAL_LIMIT=100*1024*1024;
export const POST_DOCUMENT_ACCEPT='.pdf,.txt,.md,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip';
export function publicationKind(file:{type:string}):PostAttachment['kind']{return file.type.startsWith('video/')?'video':file.type.startsWith('image/')?'image':'file'}
export function validatePublicationFiles(files:{size:number;type:string;name:string}[],existingCount=0,existingBytes=0){
 const documents=files.filter(f=>publicationKind(f)==='file');
 for(const file of documents){if(!/\.(pdf|txt|md|csv|docx?|xlsx?|pptx?|zip)$/i.test(file.name))throw Error('«'+file.name+'»: поддерживаются PDF, текст, документы Office и ZIP.');}
 // Keep the same media count and byte limits, including documents.
 validatePostFiles(files.map(f=>publicationKind(f)==='file'?{name:f.name,size:f.size,type:'image/document'}:f),existingCount,existingBytes);
}
export function attachmentsOf(post?:FeedPost):PostAttachment[]{
 if(!post)return [];
 if(post.attachments!==undefined)return post.attachments;
 return post.blobId?[{id:post.blobId,blobId:post.blobId,kind:post.video?'video':'image',name:post.video?'Видео':'Фото',mime:post.video?'video/mp4':'image/jpeg'}]:[];
}
export function validatePostFiles(files:{size:number;type:string;name:string}[],existingCount=0,existingBytes=0){
 if(files.length+existingCount>POST_MEDIA_LIMIT)throw Error('В публикации может быть до 10 вложений.');
 for(const file of files){if(!/^(image|video)\//.test(file.type))throw Error('«'+file.name+'»: выберите фото или видео.');if(!file.size)throw Error('«'+file.name+'»: файл пуст.');if(file.size>POST_FILE_LIMIT)throw Error('«'+file.name+'»: максимум 25 МБ на файл.');}
 if(files.reduce((n,f)=>n+f.size,0)+existingBytes>POST_TOTAL_LIMIT)throw Error('Выбранные файлы занимают больше 100 МБ.');
}
export function putFeedPost(state:State,post:FeedPost):State{
 if(!post.text.trim()&&!attachmentsOf(post).length)throw Error('Добавьте текст или вложение.');
 const channel=post.channelId?state.spaces.find(s=>s.id===post.channelId&&s.kind==='channel'&&!s.demo):undefined;
 if(post.channelId&&!channel)throw Error('Канал недоступен. Сохраните текст и выберите другой канал.');
 const next={...post,author:channel?.name||state.profile,text:post.text.trim()};
 let messages=state.messages;
 if(channel){const media=attachmentsOf(next)[0];const message={id:post.id,spaceId:channel.id,text:next.text,sender:state.profile,own:true,createdAt:post.createdAt,edited:!!post.updatedAt,kind:media?.kind==='video'?'video' as const:media?'file' as const:'text' as const,blobId:media?.blobId,fileName:media?.name,mime:media?.mime};messages=[...state.messages.filter(m=>m.id!==post.id),message];}
 const editing=(state.feedPosts||[]).some(p=>p.id===post.id);
 const postDrafts={...state.postDrafts};delete postDrafts[editing?post.id:'new'];
 return {...state,feedPosts:[next,...(state.feedPosts||[]).filter(p=>p.id!==post.id)],messages,postDrafts,drafts:editing?state.drafts:{...state.drafts,'feed-composer':''}};
}
export function savePostToCapsule(state:State,post:FeedPost,collection:string,tags:string[],now=Date.now()):State{
 const id='saved-post-'+post.id,old=state.capsule?.find(e=>e.id===id);
 const snapshot={...post,attachments:post.attachments?.map(a=>({...a})),images:post.images?[...post.images]:undefined};
 const entry={id,title:post.text.split('\n')[0].slice(0,100)||'Публикация '+post.author,text:post.text,collection:collection.trim()||'Из ленты',kind:'note' as const,post:snapshot,tags,createdAt:old?.createdAt||now,updatedAt:now,pinned:old?.pinned};
 return {...state,capsule:[entry,...(state.capsule||[]).filter(e=>e.id!==id)],feedSaved:[...new Set([...(state.feedSaved||[]),post.id])]};
}

export function removeFeedPost(state:State,id:string):State{
 const post=state.feedPosts?.find(p=>p.id===id);
 if(!post)return state;
 return {...state,feedPosts:state.feedPosts?.filter(p=>p.id!==id),messages:post.channelId?state.messages.filter(m=>m.id!==id||m.spaceId!==post.channelId):state.messages};
}
