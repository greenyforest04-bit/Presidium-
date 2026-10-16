import type {Message,State} from './domain';
import {recordId} from './presidium';
import {validateVoice,type VoiceDraft} from './matrix-voice';
import {saveMediaState,type MediaBytes} from './storage';

export type LocalVoice={message:Omit<Message,'sender'>;media:MediaBytes};
export async function prepareLocalVoice(draft:VoiceDraft,spaceId:string,replyTo=''):Promise<LocalVoice>{
 const voice=validateVoice(draft.file,draft.metadata);
 const bytes=await draft.file.arrayBuffer();
 if(bytes.byteLength!==draft.file.size)throw new DOMException('Не удалось прочитать запись полностью','NotReadableError');
 const blobId=recordId();
 return {
  message:{id:recordId(),spaceId,text:'',own:true,kind:'audio',createdAt:Date.now(),blobId,fileName:draft.file.name,mime:draft.file.type,voice,...(replyTo?{replyTo}:{})},
  media:{format:'presidium-bytes-v1',bytes,mime:draft.file.type},
 };
}
export async function saveLocalVoice(state:State,voice:LocalVoice):Promise<State>{
 if(!state.spaces.some(space=>space.id===voice.message.spaceId))throw new DOMException('Разговор больше не существует','NotFoundError');
 const next:State={...state,messages:[...state.messages,{...voice.message,sender:state.profile}]};
 await saveMediaState(next,voice.message.blobId!,voice.media);
 return next;
}
export function localVoiceError(problem:unknown){
 const name=(problem as {name?:string}|null)?.name;
 if(name==='QuotaExceededError')return 'Недостаточно места для голосового. Запись осталась в плеере: скачайте её или освободите память и повторите отправку.';
 if(name==='SecurityError'||name==='NotAllowedError')return 'Браузер запретил сохранение на устройстве. Запись осталась в плеере — её можно скачать.';
 if(name==='NotFoundError')return 'Разговор больше недоступен. Скачайте запись из плеера, чтобы сохранить её.';
 if(name==='NotReadableError')return 'Браузер не смог прочитать аудиозапись. Попробуйте скачать её из плеера.';
 const code=['InvalidStateError','UnknownError','AbortError','DataCloneError'].includes(name||'')?name:'StorageError';
 return `Не удалось сохранить голосовое (${code}). Запись осталась в плеере. Повторите отправку или скачайте её.`;
}
