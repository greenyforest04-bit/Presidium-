import type {NetworkMessage} from './matrix-transport';
import {normalizeMessageSearch} from './matrix-conversation';
import {encryptedFile,previewMime} from './matrix-media';

export type RoomContentKind='media'|'files'|'links';
export type RoomContentItem={key:string;kind:RoomContentKind;message:NetworkMessage;title:string;href?:string;host?:string};

export function acceptedContent(message:NetworkMessage){
 return message.encrypted&&message.readable&&message.status==='received'&&!message.failed&&!message.editTarget&&message.deletion!=='sending'&&message.deletion!=='accepted'&&/^\$[^\s]{1,511}$/.test(message.id);
}
export function attachmentRevision(message:NetworkMessage|undefined){
 if(!message||!acceptedContent(message)||!message.attachment||!encryptedFile(message.attachment.file))return;
 const {file,name,mime}=message.attachment;
 return JSON.stringify([message.id,message.editRevision,file,name,previewMime(mime)]);
}
export function safeHttpUrl(value:string){
 if(typeof value!=='string'||value.length>2048||!/^https?:\/\//i.test(value)||/[\s\u0000-\u001f\u007f\\]/.test(value))return;
 try{const url=new URL(value);if(!['https:','http:'].includes(url.protocol)||!url.hostname||url.username||url.password)return;return url.href}catch{return}
}
export function messageLinks(body:string){
 const links=new Set<string>();
 for(const match of body.matchAll(/https?:\/\/[^\s<>"'`]+/gi)){
  let candidate=match[0].replace(/[.,!?:;…]+$/u,'');
  for(const [open,close] of [['(',')'],['[',']'],['{','}']])while(candidate.endsWith(close)&&candidate.split(close).length>candidate.split(open).length)candidate=candidate.slice(0,-1);
  const href=safeHttpUrl(candidate);if(href)links.add(href);if(links.size===50)break;
 }
 return [...links];
}
export function roomContent(messages:NetworkMessage[]):RoomContentItem[]{
 const items:RoomContentItem[]=[];
 for(const message of messages){
  if(!acceptedContent(message))continue;
  if(attachmentRevision(message)){
   const mime=previewMime(message.attachment!.mime),kind=mime.startsWith('image/')||mime.startsWith('video/')?'media':'files';
   items.push({key:JSON.stringify([message.id,'attachment']),kind,message,title:message.attachment!.name});
  }
  // Filenames and legacy reply fallbacks are not external-link messages.
  if(!message.attachment)for(const href of messageLinks(message.body))items.push({key:JSON.stringify([message.id,href]),kind:'links',message,title:href,href,host:new URL(href).host});
 }
 return items.sort((a,b)=>(Number.isFinite(b.message.time)?b.message.time:0)-(Number.isFinite(a.message.time)?a.message.time:0)||a.key.localeCompare(b.key));
}
export function filterRoomContent(items:RoomContentItem[],kind:RoomContentKind,query:string){
 const term=normalizeMessageSearch(query);
 return items.filter(item=>item.kind===kind&&(!term||normalizeMessageSearch([item.title,item.host||'',item.message.sender,item.message.body].join(' ')).includes(term)));
}
