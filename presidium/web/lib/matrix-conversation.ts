import type {MatrixEvent,Room,MsgType,RelationType} from 'matrix-js-sdk';
import type {NetworkMessage} from './matrix-transport';
// Read SDK message content after decryption; do not fall back to wire metadata.
export function replyEventId(content:Record<string,unknown>){
 const relation=content['m.relates_to'] as {'m.in_reply_to'?:{event_id?:unknown}}|undefined;
 const id=relation?.['m.in_reply_to']?.event_id;
 return typeof id==='string'&&/^\$\S{1,511}$/.test(id)?id:undefined;
}
export function messageBody(content:Record<string,unknown>){
 const body=typeof content.body==='string'?content.body:'';
 if(!replyEventId(content)||!body.startsWith('> '))return body;
 // Legacy Matrix replies quoted the source at the beginning of body.
 const lines=body.split('\n');let start=0;while(lines[start]?.startsWith('> '))start++;
 if(lines[start]==='')start++;return lines.slice(start).join('\n');
}
export function canReplyTo(event:MatrixEvent,roomId:string){
 return event.getRoomId()===roomId&&!!event.getId()?.match(/^\$\S{1,511}$/)&&!event.status&&!event.isState()&&!event.threadRootId&&event.getType()==='m.room.message'&&event.isEncrypted()&&!event.isDecryptionFailure()&&!event.isRedacted()&&!replacementTarget(event);
}
export function replacementTarget(event:MatrixEvent){
 const relation=event.getOriginalContent()['m.relates_to'];
 return relation?.rel_type==='m.replace'&&typeof relation.event_id==='string'?relation.event_id:undefined;
}
// Do not let an SDK optimistic local edit hide the last accepted text. Ignore
// invalid, plaintext, cross-room, foreign-sender and redacted replacements.
export function acceptedReplacement(event:MatrixEvent,events:MatrixEvent[]){
 if(event.status||event.getType()!=='m.room.message'||!event.isEncrypted()||event.isDecryptionFailure()||event.isRedacted()||event.isState()||replacementTarget(event))return undefined;
 const candidates=[...events,event.replacingEvent()].filter((edit):edit is MatrixEvent=>!!edit&&
  !edit.status&&!!edit.getId()?.match(/^\$\S{1,511}$/)&&!edit.isState()&&!edit.isRedacted()&&edit.isEncrypted()&&!edit.isDecryptionFailure()&&
  edit.getType()===event.getType()&&edit.getRoomId()===event.getRoomId()&&edit.getSender()===event.getSender()&&
  replacementTarget(edit)===event.getId()&&!!edit.getOriginalContent()['m.new_content']&&
  typeof edit.getOriginalContent()['m.new_content']==='object'&&!Array.isArray(edit.getOriginalContent()['m.new_content'])&&typeof edit.getOriginalContent()['m.new_content'].msgtype==='string'&&typeof edit.getOriginalContent()['m.new_content'].body==='string');
 return candidates.sort((a,b)=>a.getTs()-b.getTs()||((a.getId()||'')<(b.getId()||'')?-1:1)).at(-1);
}
export function messageContent(event:MatrixEvent,events:MatrixEvent[]){
 const original=event.getOriginalContent(),edit=acceptedReplacement(event,events);
 if(!edit)return original;
 // Relationships belong to the original event, not the edit.
 return {...edit.getOriginalContent()['m.new_content'],'m.relates_to':original['m.relates_to']};
}
export function editRevision(event:MatrixEvent,events:MatrixEvent[]){return JSON.stringify([messageBody(messageContent(event,events)),acceptedReplacement(event,events)?.getId()||''])}
export function messagePermissions(event:MatrixEvent,room:Room,userId:string,events:MatrixEvent[]){
 const accepted=canReplyTo(event,room.roomId)&&room.getMyMembership()==='join'&&room.hasEncryptionStateEvent();
 const pendingEdit=events.some(edit=>!!edit.status&&edit.getSender()===userId&&replacementTarget(edit)===event.getId());
 return {editable:accepted&&!pendingEdit&&event.getSender()===userId&&event.getOriginalContent().msgtype==='m.text'&&messageContent(event,events).msgtype==='m.text'&&room.currentState.maySendEvent('m.room.encrypted',userId),deletable:accepted&&!pendingEdit&&room.currentState.maySendRedactionForEvent(event,userId)};
}
export function textReplacement(event:MatrixEvent,room:Room,userId:string,events:MatrixEvent[],body:string,expectedRevision:string){
 if(!messagePermissions(event,room,userId,events).editable)throw Error('Это сообщение нельзя редактировать. Проверьте права и состояние отправки.');
 if(editRevision(event,events)!==expectedRevision)throw Error('Сообщение изменилось на другом устройстве. Откройте редактирование заново.');
 if(!body.trim()||body.length>4000)throw Error('Сообщение: от 1 до 4000 символов.');
 if(body===messageBody(messageContent(event,events)))throw Error('Текст не изменился.');
 const content={msgtype:'m.text' as MsgType.Text,body,'m.mentions':{}};
 return {msgtype:'m.text' as MsgType.Text,body:'* '+body,'m.new_content':content,'m.mentions':{},'m.relates_to':{rel_type:'m.replace' as RelationType.Replace,event_id:event.getId()!}};
}
export function replyRelation(event:MatrixEvent|undefined,roomId:string){
 if(!event||!canReplyTo(event,roomId))throw Error('Исходное сообщение недоступно для ответа. Загрузите его или отмените ответ.');
 return {'m.relates_to':{'m.in_reply_to':{event_id:event.getId()!}}};
}
export function normalizeMessageSearch(text:string){return text.normalize('NFKC').toLocaleLowerCase('ru-RU').replace(/ё/g,'е').replace(/\s+/g,' ').trim()}
export function searchMessages(messages:NetworkMessage[],query:string){
 const term=normalizeMessageSearch(query);if(!term)return [];
 return messages.filter(message=>message.readable&&!message.editTarget&&normalizeMessageSearch(message.body).includes(term)).map(message=>message.id);
}
export function nextSearchMatch(ids:string[],current:string,direction:1|-1){
 if(!ids.length)return '';const index=ids.indexOf(current);
 return ids[index<0?(direction===1?0:ids.length-1):(index+direction+ids.length)%ids.length];
}
