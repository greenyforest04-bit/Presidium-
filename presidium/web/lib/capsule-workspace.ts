import type {CapsuleEntry,NotebookBlock,State} from './domain';
import {recordId} from './presidium';
import {putCapsule} from './capsule';

export function blocksText(blocks:NotebookBlock[]){return blocks.map(b=>b.kind==='divider'?'---':b.kind==='task'?`[${b.checked?'x':' '}] ${b.text}`:b.text).join('\n\n')}
export function notebookBlocks(entry:CapsuleEntry):NotebookBlock[]{return entry.blocks?.map(b=>({...b}))??[{id:recordId(),kind:'text',text:entry.text}]}
export function putNotebook(state:State,entry:CapsuleEntry){
 if(!entry.blocks||entry.blocks.length>200||entry.blocks.some(b=>!b.id||!['text','heading','task','quote','divider'].includes(b.kind)||b.text.length>10000)||new Set(entry.blocks.map(b=>b.id)).size!==entry.blocks.length)throw Error('Проверьте блоки страницы. Максимум — 200 блоков.');
 return putCapsule(state,{...entry,text:blocksText(entry.blocks)});
}
export function moveNotebookBlock(blocks:NotebookBlock[],id:string,delta:number){const index=blocks.findIndex(b=>b.id===id),target=index+delta;if(index<0||target<0||target>=blocks.length)return blocks;const next=[...blocks];[next[index],next[target]]=[next[target],next[index]];return next}
export function notebookPages(data:State){return (data.capsule||[]).filter(e=>!e.deletedAt&&e.kind==='note'&&!e.story&&!e.post&&!e.blobId)}
export function knowledgeMatches(data:State,query:string){const terms=query.toLocaleLowerCase('ru').split(/[^\p{L}\p{N}]+/u).filter(t=>t.length>1);return (data.capsule||[]).filter(e=>!e.deletedAt).map(entry=>({entry,score:terms.reduce((score,t)=>score+(entry.title.toLocaleLowerCase('ru').includes(t)?3:0)+((entry.text+' '+(entry.tags||[]).join(' ')).toLocaleLowerCase('ru').includes(t)?1:0),0)})).filter(r=>r.score>0).sort((a,b)=>b.score-a.score).slice(0,12).map(r=>r.entry)}
export function putMeeting(state:State,entry:CapsuleEntry){
 const meeting=entry.meeting;
 if(!meeting||!Number.isFinite(Date.parse(meeting.startsAt))||meeting.participants.length>50||!entry.title.trim())throw Error('Укажите название и время встречи.');
 return putCapsule(state,{...entry,kind:'task',collection:'Встречи'});
}
export function meetingCalendar(entry:CapsuleEntry){
 if(!entry.meeting)throw Error('У встречи нет времени.');
 const escape=(s:string)=>s.replace(/\\/g,'\\\\').replace(/\r?\n/g,'\\n').replace(/;/g,'\\;').replace(/,/g,'\\,');
 const stamp=(time:number)=>new Date(time).toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'');
 const start=Date.parse(entry.meeting.startsAt);
 return ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Presidium//Capsule//RU','BEGIN:VEVENT','UID:'+escape(entry.id)+'@presidium','DTSTAMP:'+stamp(Date.now()),'DTSTART:'+stamp(start),'DTEND:'+stamp(start+3600000),'SUMMARY:'+escape(entry.title),'DESCRIPTION:'+escape(entry.text),'END:VEVENT','END:VCALENDAR',''].join('\r\n');
}
