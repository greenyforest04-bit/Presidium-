import {useEffect,useState} from 'react'
import type {MatrixTransport} from '../core/messaging/MatrixTransport'
import type {MessageEvent} from '../core/messaging/MessagingTransport'

export default function GroupChat({transport}:{transport:MatrixTransport}) {
  const [rooms,setRooms]=useState(()=>transport.listGroupConversations()),[active,setActive]=useState('')
  const [name,setName]=useState(''),[users,setUsers]=useState(''),[text,setText]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  const [messages,setMessages]=useState<MessageEvent[]>([])
  useEffect(()=>{const timer=setInterval(()=>setRooms(transport.listGroupConversations()),2000);return()=>clearInterval(timer)},[transport])
  useEffect(()=>{
    if(!active)return
    let cancelled=false
    const add=(event:MessageEvent)=>setMessages(old=>old.some(e=>e.eventId===event.eventId)?old:[...old,event].sort((a,b)=>a.timestamp-b.timestamp))
    const off=transport.subscribeToMessages(active,add)
    void transport.getMessages(active).then(events=>{if(!cancelled)events.forEach(add)}).catch(e=>{if(!cancelled)setError(String(e))})
    return()=>{cancelled=true;off()}
  },[active,transport])
  const act=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();setRooms(transport.listGroupConversations())}catch(e){setError(String(e))}finally{setBusy(false)}}
  return <article className="product-card" aria-label="Групповой чат Matrix">
    <h3>Зашифрованные групповые чаты</h3>
    <p className="muted">Переписка через тот же Matrix и E2EE. Состав чата управляется Matrix; участники пространства ленты приглашаются отдельно.</p>
    {error&&<p role="alert">{error}</p>}
    <label>Название группового чата<input value={name} onChange={e=>setName(e.target.value)}/></label>
    <label>Matrix ID участников через запятую<input value={users} onChange={e=>setUsers(e.target.value)}/></label>
    <button disabled={busy} onClick={()=>void act(async()=>{const id=await transport.createGroupConversation(name,users.split(',').map(s=>s.trim()).filter(Boolean));setActive(id);setName('');setUsers('')})}>Создать зашифрованный чат</button>
    {rooms.map(room=><div key={room.id}><button disabled={busy} onClick={()=>void act(async()=>{if(room.invited)await transport.joinGroupConversation(room.id);else await transport.assertGroupConversation(room.id);setActive(room.id)})}>{room.invited?'Принять приглашение: ':'Открыть чат: '}{room.name}</button></div>)}
    {active&&<div data-group-room={active}>
      <div className="product-group-messages" aria-label="Сообщения группы">{messages.filter(event=>event.roomId===active).map(event=><p key={event.eventId} data-event-id={event.eventId}><strong>{event.sender}</strong>: {event.body}</p>)}</div>
      <label>Сообщение группе<textarea value={text} onChange={e=>setText(e.target.value)}/></label>
      <button disabled={busy||!text.trim()} onClick={()=>void act(async()=>{await transport.sendGroupMessage(active,text);setText('')})}>Отправить в группу</button>
    </div>}
  </article>
}
