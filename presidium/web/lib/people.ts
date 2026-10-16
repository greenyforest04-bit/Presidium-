import {Contact,State,initials} from './presidium';
export type CommitState=(update:(state:State)=>State)=>Promise<void>;
export function contactsOf(data:State):Contact[]{return data.contacts??data.spaces.filter(s=>s.kind==='chat').map(s=>({id:s.id,name:s.name,phone:'',handle:s.description.startsWith('@')?s.description.split(' ')[0]:'',bio:'',spaceId:s.id,avatarId:s.avatarId}));}
export function cleanContact(c:Contact):Contact{return {...c,name:c.name.trim(),phone:c.phone.trim(),handle:c.handle.trim().replace(/^@/,''),bio:c.bio.trim()};}
export function contactError(contact:Contact,state:State){
 const c=cleanContact(contact);if(!c.name)return 'Укажите имя контакта.';
 if(c.handle&&!/^[\p{L}\p{N}_\.]{1,64}$/u.test(c.handle))return 'В имени пользователя допустимы буквы, цифры, точка и подчёркивание.';
 const digits=c.phone.replace(/\D/g,'');
 if(c.phone&&digits.length<5)return 'Проверьте номер телефона.';
 const duplicate=contactsOf(state).find(x=>x.id!==c.id&&((digits&&x.phone.replace(/\D/g,'')===digits)||(c.handle&&x.handle.replace(/^@/,'').toLowerCase()===c.handle.toLowerCase())));
 return duplicate?'Эти контактные данные уже указаны у «'+duplicate.name+'».':'';
}
export function saveContact(state:State,contact:Contact):State{
 const c=cleanContact(contact),error=contactError(c,state);if(error)throw Error(error);
 return {...state,contacts:[...contactsOf(state).filter(x=>x.id!==c.id),c],spaces:state.spaces.map(s=>s.id===c.spaceId?{...s,name:c.name,initials:initials(c.name),avatarId:c.avatarId??s.avatarId}:s)};
}
export function matchesContact(c:Contact,query:string){const q=query.trim().toLocaleLowerCase('ru');return !q||[c.name,c.handle,c.phone,c.bio].join(' ').toLocaleLowerCase('ru').includes(q)||(/\d/.test(q)&&/^\+?[\d\s()\-]+$/.test(q)&&c.phone.replace(/\D/g,'').includes(q.replace(/\D/g,'')));}
export function contactVCard(c:Contact){const escape=(s:string)=>s.replace(/\\/g,'\\\\').replace(/\r?\n/g,'\\n').replace(/;/g,'\\;').replace(/,/g,'\\,');return ['BEGIN:VCARD','VERSION:3.0','FN:'+escape(c.name),...(c.phone?['TEL:'+escape(c.phone)]:[]),...(c.handle?['X-SOCIALPROFILE:'+escape('@'+c.handle.replace(/^@/,''))]:[]),'END:VCARD',''].join('\r\n');}
