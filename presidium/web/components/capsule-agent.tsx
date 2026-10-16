'use client';
import {useEffect,useRef,useState} from 'react';
import {Bot,Search,Send,Square,Bookmark,FileText,Check,RefreshCw} from 'lucide-react';
import type {State} from '@/lib/domain';
import type {CommitState} from '@/lib/people';
import {knowledgeMatches} from '@/lib/capsule-workspace';
import {putCapsule} from '@/lib/capsule';
import {recordId} from '@/lib/presidium';
export function CapsuleAgent({data,commit,onPage,onAccount}:{data:State;commit:CommitState;onPage:(id:string)=>void;onAccount:()=>void}){
 const [question,setQuestion]=useState(''),[selected,setSelected]=useState<string[]>([]),[answer,setAnswer]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState(false),[error,setError]=useState(''),[configured,setConfigured]=useState<boolean|null>(null);
 const controller=useRef<AbortController|undefined>(undefined),alive=useRef(true),saving=useRef(false);
 useEffect(()=>{alive.current=true;void check();return()=>{alive.current=false;controller.current?.abort()}},[]);
 async function check(){try{const response=await fetch('/api/capsule-agent',{cache:'no-store'});const result=await response.json() as {configured?:unknown};if(alive.current)setConfigured(response.ok&&result.configured===true)}catch{if(alive.current)setConfigured(false)}}
 const active=(data.capsule||[]).filter(e=>!e.deletedAt),found=question.trim()?knowledgeMatches(data,question):active.slice(0,8),matches=[...active.filter(e=>selected.includes(e.id)),...found.filter(e=>!selected.includes(e.id))];
 async function ask(){
  if(busy||!question.trim())return;const abort=new AbortController();controller.current=abort;setBusy(true);setError('');setSaved(false);setAnswer('');
  try{
   const materials=selected.flatMap(id=>{const entry=active.find(e=>e.id===id);return entry?[{id,title:entry.title.slice(0,120),text:entry.text.slice(0,600)}]:[]});
   const payload={requestId:crypto.randomUUID(),question,materials};if(new TextEncoder().encode(JSON.stringify(payload)).byteLength>7500)materials.forEach(m=>{m.text=m.text.slice(0,250)});
   const response=await fetch('/api/capsule-agent',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:abort.signal});const result=await response.json() as {answer?:unknown;error?:unknown};if(!response.ok)throw Error(typeof result.error==='string'?result.error:'Не удалось получить ответ.');if(typeof result.answer!=='string'||!result.answer.trim()||result.answer.length>10000)throw Error('Некорректный ответ агента.');if(alive.current)setAnswer(result.answer);
  }catch(e){if(alive.current)setError(abort.signal.aborted?'Запрос остановлен.':e instanceof Error?e.message:'Нет связи с агентом.')}
  finally{if(alive.current)setBusy(false);if(controller.current===abort)controller.current=undefined}
 }
 async function save(){if(saving.current||!answer)return;saving.current=true;try{await commit(s=>putCapsule(s,{id:recordId(),title:question.slice(0,120),text:answer,kind:'note',collection:'Ответы агента',tags:['агент'],createdAt:Date.now()}));if(alive.current)setSaved(true)}catch(e){if(alive.current)setError(e instanceof Error?e.message:'Не удалось сохранить ответ.')}finally{saving.current=false}}
 return <div className="cu-agent"><div className="cu-agent-title"><span><Bot size={27}/></span><div><h2>ИИ-агент</h2><p>Работа с выбранными знаниями и страницами</p></div><button className="icon-btn" aria-label="Проверить подключение агента" onClick={()=>void check()}><RefreshCw size={18}/></button></div>
 <div className="cu-agent-status" role="status">{configured===null?'Проверяем подключение…':configured?'ИИ-сервис подключён':'ИИ-модель ещё не подключена'}{configured===false&&<p>Поиск по вашим материалам уже работает. Для ответов агента требуется подключить ИИ-сервис к Presidium.</p>}</div>
 <div className="cu-agent-prompts">{['Найди мои идеи','Помоги составить план','Подведи итоги заметок'].map(q=><button key={q} onClick={()=>setQuestion(q)}>{q}</button>)}</div>
 <label className="cu-agent-question">Запрос<textarea rows={3} maxLength={800} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="Что нужно найти или разобрать?"/></label>
 <div className="cu-section-heading"><h3><Search size={17}/>Мои материалы</h3><small>Выбрано {selected.length} / 4</small></div><p className="cu-muted">Агент получит только выбранные текстовые фрагменты. Переписка и файлы автоматически не отправляются.</p>
 <div className="cu-source-list">{matches.map(entry=><div key={entry.id}><input type="checkbox" aria-label={'Передать агенту '+entry.title} checked={selected.includes(entry.id)} disabled={!selected.includes(entry.id)&&selected.length>=4} onChange={e=>setSelected(s=>e.target.checked?[...s,entry.id]:s.filter(id=>id!==entry.id))}/><button onClick={()=>onPage(entry.id)}><FileText size={19}/><span><b>{entry.title||'Без названия'}</b><small>{entry.text.slice(0,110)||entry.fileName||entry.collection}</small></span></button></div>)}</div>{!matches.length&&<p className="cu-muted">Совпадений нет. Попробуйте название, тег или слово из заметки.</p>}
 <div className="cu-agent-actions"><button className="secondary-btn" onClick={onAccount}>Серверный профиль</button>{busy?<button className="secondary-btn" onClick={()=>controller.current?.abort()}><Square size={16}/>Остановить</button>:<button className="primary-btn" disabled={!configured||!question.trim()} onClick={()=>void ask()}><Send size={17}/>Спросить агента</button>}</div>
 {error&&<p className="cu-error" role="alert">{error}</p>}{answer&&<article className="cu-agent-answer"><h3><Bot size={19}/>Ответ агента</h3><p>{answer}</p><button className="secondary-btn" disabled={saved} onClick={()=>void save()}>{saved?<Check size={17}/>:<Bookmark size={17}/>} {saved?'Сохранено в библиотеку':'Сохранить ответ'}</button></article>}</div>;
}
