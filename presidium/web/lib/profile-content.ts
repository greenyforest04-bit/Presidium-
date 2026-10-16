import type {State,FeedPost,Message} from './domain';
import {attachmentsOf} from './feed';

/** feedPosts is the owner's local publishing collection, independent of display-name changes. */
export function profilePosts(data:State):FeedPost[]{
 return [...(data.feedPosts||[])].filter(p=>!p.demo).map(p=>({...p,author:p.channelId?data.spaces.find(s=>s.id===p.channelId)?.name||p.author:data.profile})).sort((a,b)=>b.createdAt-a.createdAt);
}
export function profileMedia(data:State,target='me'):{media:Message[];files:Message[]}{
 const own=target==='me';
 const messages=data.messages.filter(m=>own?m.own:m.spaceId===target);
 const published:Message[]=own?profilePosts(data).flatMap(p=>attachmentsOf(p).map(a=>({id:`post:${p.id}:${a.id}`,spaceId:p.channelId||'feed',text:p.text,sender:data.profile,own:true,createdAt:p.createdAt,kind:a.kind==='video'?'video' as const:'file' as const,blobId:a.blobId,fileName:a.name,mime:a.mime}))):[];
 const seen=new Set<string>();
 const unique=[...published,...messages].filter(m=>{if(!m.blobId||seen.has(m.blobId))return false;seen.add(m.blobId);return true}).sort((a,b)=>b.createdAt-a.createdAt);
 const media=unique.filter(m=>m.mime?.startsWith('image/')||m.kind==='video');
 return {media,files:unique.filter(m=>!media.includes(m))};
}
