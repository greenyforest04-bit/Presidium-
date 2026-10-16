import type {Room,MatrixEvent} from 'matrix-js-sdk';
export type MessageDelivery='encrypting'|'sending'|'queued'|'failed'|'cancelled'|'accepted'|'read'|'pending';
// Only real public receipts count. Private/synthetic/thread receipts and timestamps
// cannot establish that a peer has read a message in this timeline.
export function confirmedReaders(room:Room,event:MatrixEvent,ownUserId:string){
 const eventId=event.getId();if(!eventId?.startsWith('$')||event.threadRootId)return [];
 return room.getJoinedMembers().filter(member=>{
  if(member.userId===ownUserId)return false;
  const receipt=room.getReadReceiptForUserId(member.userId,true);
  if(!receipt||receipt.data.thread_id&&receipt.data.thread_id!=='main')return false;
  const order=receipt.eventId===eventId?0:room.compareEventOrdering(eventId,receipt.eventId);
  return order!==null&&order<=0;
 }).map(member=>member.userId);
}
export function deliveryState(status:string,eventId:string,readBy:string[],queued:boolean):MessageDelivery{
 if(status==='cancelled')return 'cancelled';if(status==='encrypting')return 'encrypting';if(status==='sending'||status==='queued')return 'sending';if(status==='not_sent')return queued?'queued':'failed';
 if(eventId.startsWith('$'))return readBy.length?'read':'accepted';return queued?'queued':'pending';
}
export function deliveryLabel(state:MessageDelivery,count=0){return {encrypting:'Шифруется',sending:'Отправляется',queued:'В очереди',failed:'Ошибка отправки',cancelled:'Отменено',accepted:'Сервер принял',read:count>1?`Прочитали: ${count}`:'Прочитано',pending:'Ожидает отправки'}[state]}
