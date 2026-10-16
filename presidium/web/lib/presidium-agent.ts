import type {State, CapsuleEntry} from './domain';
import {read,write} from './storage';

import type {AgentState} from './agent-state';
export type {AgentState} from './agent-state';
export type AgentScope='messages'|'library'|'tasks';
export type AgentMode='audit'|'messages'|'tasks'|'news';
export type AgentCategory='urgent'|'messages'|'tasks'|'documents'|'news'|'opportunities'|'recommendations';
export type AgentEvent={id:string;label:string;status:'running'|'success'|'error'|'paused';at:number;detail?:string};
export type AgentItem={id:string;category:AgentCategory;title:string;text:string;createdAt:number;source:{type:'message'|'capsule';id:string;spaceId?:string}};
export type AgentReport={period:{from:number;to:number};items:AgentItem[];counts:Record<AgentCategory,number>;sources:{scope:AgentScope;label:string;count:number}[];limitations:string[]};
export type AgentConnector={id:string;name:string;status:'unconfigured'|'connected'|'error';permissions:string[];reason?:string};
export const externalConnectors:AgentConnector[]=[
 {id:'gmail',name:'Gmail',status:'unconfigured',permissions:['mail.read'],reason:'Требуются регистрация OAuth-приложения и разрешение владельца почты.'},
 {id:'calendar',name:'Google Calendar',status:'unconfigured',permissions:['calendar.read'],reason:'Требуются OAuth-приложение и разрешение владельца календаря.'},
 {id:'telegram',name:'Telegram',status:'unconfigured',permissions:[],reason:'Не подключён поддерживаемый API. Доступ к личным перепискам отсутствует.'},
 {id:'youtube',name:'YouTube',status:'unconfigured',permissions:['youtube.read'],reason:'Требуются API-проект и OAuth для данных пользователя.'},
 {id:'rss',name:'Новости / RSS',status:'unconfigured',permissions:['news.read'],reason:'Источники новостей ещё не добавлены.'},
 {id:'meta',name:'Meta',status:'unconfigured',permissions:[],reason:'Требуются одобренное приложение и поддерживаемый тип аккаунта.'},
];
const categories:AgentCategory[]=['urgent','messages','tasks','documents','news','opportunities','recommendations'];
const scopeLabels:Record<AgentScope,string>={messages:'Сообщения на устройстве',library:'Библиотека Капсулы',tasks:'Задачи Капсулы'};
export class AgentPermissionManager {
 private allowed:Set<AgentScope>;
 constructor(scopes:readonly AgentScope[]){this.allowed=new Set(scopes)}
 permits(scope:AgentScope){return this.allowed.has(scope)}
 revoke(scope:AgentScope){this.allowed.delete(scope)}
 require(scope:AgentScope){if(!this.permits(scope))throw new Error(`Нет разрешения: ${scopeLabels[scope]}`)}
}
export const agentTools=[
 {id:'read_messages',scope:'messages',readOnly:true,label:'Прочитать сообщения на устройстве'},
 {id:'read_library',scope:'library',readOnly:true,label:'Прочитать материалы библиотеки'},
 {id:'read_tasks',scope:'tasks',readOnly:true,label:'Прочитать задачи'},
 {id:'create_note',scope:'library',readOnly:false,label:'Сохранить заметку после подтверждения'},
 {id:'create_task',scope:'tasks',readOnly:false,label:'Создать задачу после подтверждения'},
] as const;
const aborted=()=>new DOMException('Выполнение отменено','AbortError');
const frame=()=>new Promise<void>(resolve=>setTimeout(resolve,0));
function check(signal?:AbortSignal){if(signal?.aborted)throw aborted()}
/** Local source audit only. No network, LLM, credentials, fabricated importance or unread state. */
export async function runLocalAudit(options:{state:State;mode:AgentMode;scopes:readonly AgentScope[];signal?:AbortSignal;waitIfPaused?:()=>Promise<void>;onState?:(state:AgentState)=>void;onEvent?:(event:AgentEvent)=>void;now?:number;periodMs?:number}):Promise<AgentReport>{
 const now=options.now??Date.now(),period=Math.min(7*86400000,Math.max(60000,options.periodMs??86400000)),started=Date.now();
 const report:AgentReport={period:{from:now-period,to:now},items:[],counts:Object.fromEntries(categories.map(c=>[c,0])) as Record<AgentCategory,number>,sources:[],limitations:['Локальный обзор доступных записей. Срочность и рекомендации моделью не оценивались.','Сетевая переписка, почта и внешние социальные сети в этот обзор не входят.']};
 const permissions=new AgentPermissionManager(options.scopes),seen=new Set<string>();let steps=0;
 const emit=(id:string,label:string,status:AgentEvent['status'],detail?:string)=>options.onEvent?.({id,label,status,detail,at:Date.now()});
 const checkpoint=async()=>{check(options.signal);if(Date.now()-started>45000)throw new Error('Лимит выполнения: 45 секунд');if(options.waitIfPaused){let timer:ReturnType<typeof setTimeout>|undefined;let rejectAbort:(()=>void)|undefined;try{await Promise.race([options.waitIfPaused(),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('Лимит выполнения: 45 секунд')),Math.max(1,45000-(Date.now()-started)));rejectAbort=()=>reject(aborted());options.signal?.addEventListener('abort',rejectAbort,{once:true})})])}finally{if(timer)clearTimeout(timer);if(rejectAbort)options.signal?.removeEventListener('abort',rejectAbort)}}check(options.signal);await frame();};
 const add=(item:AgentItem)=>{if(seen.has(item.id)||item.createdAt<report.period.from||item.createdAt>now)return;seen.add(item.id);report.items.push(item);report.counts[item.category]++};
 async function step(scope:AgentScope,collect:()=>Promise<void>){if(!permissions.permits(scope))return;if(++steps>8)throw new Error('Лимит инструментов: 8 шагов');await checkpoint();permissions.require(scope);options.onState?.('EXECUTING');const id=`read_${scope}`;emit(id,scopeLabels[scope],'running');const before=report.items.length;try{await collect();const count=report.items.length-before;report.sources.push({scope,label:scopeLabels[scope],count});emit(id,scopeLabels[scope],'success',`${count} записей за выбранный период`)}catch(error){emit(id,scopeLabels[scope],'error',error instanceof Error?error.message:'Ошибка чтения');throw error}}
 try{
  options.onState?.('THINKING');await checkpoint();
  if(options.mode==='news'){report.limitations.push('Новостные источники не подключены. Новостной аудит не выполнен.');options.onState?.('SUCCESS');return report}
  if(options.mode==='audit'||options.mode==='messages')await step('messages',async()=>{for(let i=0;i<options.state.messages.length;i++){if(i%100===0)await checkpoint();const m=options.state.messages[i];if(m.demo||m.spaceId.startsWith('!'))continue;const space=options.state.spaces.find(s=>s.id===m.spaceId);add({id:`message:${m.id}`,category:'messages',title:`${space?.name??'Переписка'} · ${m.sender}`,text:m.text||({audio:'Голосовое сообщение',video:'Видео',file:'Файл',text:'Сообщение'}[m.kind]),createdAt:m.createdAt,source:{type:'message',id:m.id,spaceId:m.spaceId}})}});
  if(options.mode==='audit')await step('library',async()=>{const entries=options.state.capsule??[];for(let i=0;i<entries.length;i++){if(i%100===0)await checkpoint();const entry=entries[i];if(entry.deletedAt||entry.kind==='task')continue;add({id:`capsule:${entry.id}`,category:'documents',title:entry.title,text:entry.text,createdAt:entry.updatedAt??entry.createdAt,source:{type:'capsule',id:entry.id}})}});
  if(options.mode==='audit'||options.mode==='tasks')await step('tasks',async()=>{const entries=options.state.capsule??[];for(let i=0;i<entries.length;i++){if(i%100===0)await checkpoint();const entry=entries[i];if(entry.deletedAt||entry.kind!=='task'||entry.done)continue;add({id:`capsule:${entry.id}`,category:'tasks',title:entry.title,text:entry.text,createdAt:entry.updatedAt??entry.createdAt,source:{type:'capsule',id:entry.id}})}});
  await checkpoint();report.items.sort((a,b)=>b.createdAt-a.createdAt);if(!report.sources.length)report.limitations.push('Ни один доступный источник не разрешён. Выберите источники для обзора.');options.onState?.('SUCCESS');return report;
 }catch(error){options.onState?.(options.signal?.aborted||error instanceof DOMException&&error.name==='AbortError'?'CANCELLED':'ERROR');throw error}
}
export type LocalProposal={id:string;kind:'create_note'|'create_task';title:string;text:string;createdAt:number};
export function createLocalProposal(kind:LocalProposal['kind'],title:string,text:string):LocalProposal{
 if(!title.trim()||title.length>120||text.length>10000)throw new Error('Название: до 120 знаков, текст: до 10 000 знаков.');
 return {id:crypto.randomUUID(),kind,title:title.trim(),text,createdAt:Date.now()};
}
/** A proposal is inert until explicit UI confirmation; stable ID makes retries idempotent. */
export async function executeLocalProposal(proposal:LocalProposal,options:{confirmed:boolean;getState:()=>State;commit:(state:State)=>Promise<unknown>;verify?:()=>Promise<State>;signal?:AbortSignal}):Promise<CapsuleEntry>{
 if(!options.confirmed)throw new Error('Для сохранения требуется подтверждение пользователя.');check(options.signal);
 if(!['create_note','create_task'].includes(proposal.kind)||!proposal.title.trim()||proposal.title.length>120||proposal.text.length>10000||!Number.isFinite(proposal.createdAt))throw new Error('Некорректное предложение агента.');
 const state=options.getState(),id=`agent-${proposal.id}`;const existing=state.capsule?.find(e=>e.id===id);
 const item:CapsuleEntry={id,title:proposal.title,text:proposal.text,collection:'Агент',kind:proposal.kind==='create_task'?'task':'note',createdAt:proposal.createdAt};
 if(existing&&(existing.title!==item.title||existing.text!==item.text||existing.kind!==item.kind||existing.deletedAt))throw new Error('Конфликт сохранённой операции. Создайте новое предложение.');
 if(!existing)await options.commit({...state,capsule:[item,...(state.capsule??[])]});
 // Cancellation after commit does not undo a user-approved write. Always verify its actual persistence.
 const persisted=options.verify?await options.verify():await read<State>('state','current');const saved=persisted?.capsule?.find(e=>e.id===id);
 if(!saved||saved.title!==item.title||saved.text!==item.text||saved.kind!==item.kind)throw new Error('Не удалось подтвердить сохранение. Повторите операцию с тем же предложением.');return saved;
}
export type AgentMemoryRecord={id:string;createdAt:number;title:string;summary:string};
export const agentMemory={
 async list():Promise<AgentMemoryRecord[]>{return (await read<AgentMemoryRecord[]>('state','agent-memory-v1'))??[]},
 async remember(record:AgentMemoryRecord){if(record.title.length>120||record.summary.length>2000)throw new Error('Слишком длинная запись памяти.');const current=await this.list();await write('state','agent-memory-v1',[record,...current.filter(r=>r.id!==record.id)].slice(0,20))},
 async clear(){await write('state','agent-memory-v1',[])},
};
export function reportText(report:AgentReport){return [`Обзор за ${new Date(report.period.from).toLocaleString('ru-RU')} — ${new Date(report.period.to).toLocaleString('ru-RU')}`,...report.items.map(item=>`${item.title}\n${item.text}`),...report.limitations].join('\n\n')}
