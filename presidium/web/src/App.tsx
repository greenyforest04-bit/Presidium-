import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { MatrixTransport } from './core/messaging/MatrixTransport'
import type { MessageEvent, UserSearchResult } from './core/messaging/MessagingTransport'
import FeatureBoundary from "./product/Boundary"
import Workspace from './product/Workspace'
import type { ProductTab } from './product/Workspace'

function App() {
  const transportRef = useRef<MatrixTransport | null>(null)
  const unsubscribeRef = useRef<(() => void) | null>(null)
  const epoch = useRef(0)
  const conversationEpoch = useRef(0)
  const activeRoom = useRef<{ id: string; recipient: UserSearchResult } | null>(null)
  const opening = useRef(false)
  const [homeserverUrl, setHomeserverUrl] = useState(import.meta.env.VITE_MATRIX_HOMESERVER_URL ?? 'http://localhost:8008')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [searchResults, setSearchResults] = useState<UserSearchResult[]>([])
  const [selectedUser, setSelectedUser] = useState<UserSearchResult | null>(null)
  const [roomId, setRoomId] = useState('')
  const [activeRecipient, setActiveRecipient] = useState<UserSearchResult | null>(null)
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState('Ready')
  const [session, setSession] = useState<{ userId: string; deviceId: string; username: string; accessToken: string; transport: MatrixTransport } | null>(null)
  const [section, setSection] = useState<ProductTab|'Чаты'>('Чаты')
  const [events, setEvents] = useState<MessageEvent[]>([])
  const [secure, setSecure] = useState(false)
  const [busy, setBusy] = useState(false)
  const activeUserKey = `presidium_active_user_${encodeURIComponent(homeserverUrl.replace(/\/$/, ''))}`
  const recipientKey = (userId: string) => `presidium_recipient_${encodeURIComponent(homeserverUrl)}_${encodeURIComponent(userId)}`
  const errorText = (error: unknown) => error instanceof Error ? error.message : 'Request failed'

  const reset = () => {
    setSection('Чаты')
    unsubscribeRef.current?.()
    unsubscribeRef.current = null
    activeRoom.current = null
    opening.current = false
    conversationEpoch.current++
    setSession(null); setRoomId(''); setEvents([]); setSecure(false); setActiveRecipient(null); setBusy(false)
    setSelectedUser(null); setSearchResults([]); setSearchTerm(''); setMessage('')
  }

  const addMessage = (event: MessageEvent) => setEvents((previous) =>
    previous.some((entry) => entry.eventId === event.eventId) ? previous : [...previous, event])

  const activateRoom = async (transport: MatrixTransport, id: string, user: UserSearchResult, userId: string) => {
    const version = ++conversationEpoch.current
    opening.current = true
    activeRoom.current = null
    setRoomId(''); setSecure(false); setMessage('')
    try {
      await transport.assertDirectConversation(id, user.userId)
      const history = await transport.getMessages(id)
      if (transportRef.current !== transport || version !== conversationEpoch.current) return
      activeRoom.current = { id, recipient: user }
      setSelectedUser(user); setActiveRecipient(user); setRoomId(id); setSecure(true)
      setEvents((previous) => {
        const messages = new Map(previous.map((event) => [event.eventId, event]))
        history.forEach((event) => messages.set(event.eventId, event))
        return [...messages.values()].sort((a, b) => a.timestamp - b.timestamp)
      })
      window.localStorage.setItem(recipientKey(userId), user.userId)
      setStatus('Chat is encrypted')
    } finally {
      if (version === conversationEpoch.current) opening.current = false
    }
  }

  const connect = async (user: string, secret: string, version: number) => {
    const transport = new MatrixTransport(homeserverUrl)
    transportRef.current = transport
    try {
      const result = await transport.login(user, secret)
      if (version !== epoch.current) { await transport.stop(); return }
      setSession({ ...result, username: user, transport }); setUsername(user); setPassword('')
      window.localStorage.setItem(activeUserKey, user)
      const liveSince = Date.now()
      unsubscribeRef.current = transport.subscribeToMessages(null, (event) => {
        if (transportRef.current !== transport) return
        addMessage(event)
        if (!activeRoom.current && !opening.current && event.sender !== result.userId && event.timestamp >= liveSince) {
          const sender = { userId: event.sender, displayName: event.sender.split(':')[0].slice(1) }
          void activateRoom(transport, event.roomId, sender, result.userId).catch(() => undefined)
        }
      })
      setStatus(`${user[0].toUpperCase()}${user.slice(1)} logged in`)
      const recipient = window.localStorage.getItem(recipientKey(result.userId))
      if (recipient) {
        const id = await transport.createDirectConversation(recipient)
        if (version === epoch.current) await activateRoom(transport, id, { userId: recipient }, result.userId)
      }
    } catch (error) {
      await transport.stop()
      if (version === epoch.current) { reset(); transportRef.current = null; throw error }
    }
  }

  const restoreAccount = useEffectEvent((version: number) => {
    reset()
    const saved = window.localStorage.getItem(activeUserKey)
    if (saved) void connect(saved, '', version).catch((error: unknown) => {
      if (version !== epoch.current) return
      window.localStorage.removeItem(activeUserKey)
      setStatus(errorText(error))
    })
  })
  const disconnectAccount = useEffectEvent(() => {
    epoch.current++; conversationEpoch.current++
    unsubscribeRef.current?.()
    void transportRef.current?.stop()
    transportRef.current = null
  })
  useEffect(() => {
    restoreAccount(++epoch.current)
    return () => disconnectAccount()
  }, [homeserverUrl])

  useEffect(() => {
    const transport = transportRef.current
    const active = activeRoom.current
    if (!transport || !active || !roomId) return
    let cancelled = false
    const check = async () => {
      try {
        await transport.assertDirectConversation(active.id, active.recipient.userId)
        if (!cancelled) setSecure(true)
      } catch {
        if (!cancelled) { setSecure(false); setStatus('Room security changed. Sending is disabled.') }
      }
    }
    const unsubscribe = transport.subscribeToRoomChanges(roomId, () => { setSecure(false); void check() })
    const timer = setInterval(() => void check(), 10_000)
    return () => { cancelled = true; clearInterval(timer); unsubscribe() }
  }, [roomId, session])

  const handleLogin = async () => {
    if (!username.trim() || !password) { setStatus('Enter your username and password.'); return }
    const version = ++epoch.current
    const previous = transportRef.current
    transportRef.current = null
    reset(); setBusy(true); setStatus('Signing in securely...')
    await previous?.stop()
    try { await connect(username.trim(), password, version) }
    catch (error) { if (version === epoch.current) setStatus(errorText(error)) }
    finally { if (version === epoch.current) setBusy(false) }
  }

  const handleLogout = async () => {
    const transport = transportRef.current
    const user = session?.username
    epoch.current++; transportRef.current = null
    reset(); setBusy(false); setPassword('')
    window.localStorage.removeItem(activeUserKey)
    try { if (user) await transport?.logout(user); setStatus('Signed out') }
    catch (error) { setStatus(errorText(error)) }
  }

  const handleRegister = async () => {
    if (!username.trim() || password.length < 8) { setStatus('Для регистрации укажи логин и пароль от 8 символов.'); return }
    setBusy(true)
    try {
      const response = await fetch(`${homeserverUrl.replace(/\/$/, '')}/_matrix/client/v3/register`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:username.trim(),password,auth:{type:'m.login.dummy'}}),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error ?? 'Регистрация недоступна на этом сервере')
      const headers={Authorization:`Bearer ${result.access_token}`,'Content-Type':'application/json'}
      await fetch(`${homeserverUrl}/_matrix/client/v3/profile/${encodeURIComponent(result.user_id)}/displayname`,{method:'PUT',headers,body:JSON.stringify({displayname:username.trim()})})
      await fetch(`${homeserverUrl}/_matrix/client/v3/logout`,{method:'POST',headers,body:'{}'})
      await handleLogin()
    } catch(error) { setStatus(errorText(error)) }
    finally { setBusy(false) }
  }

  const handleSearch = async () => {
    const transport = transportRef.current
    if (!transport || !searchTerm.trim()) return
    try {
      const results = await transport.searchUsers(searchTerm)
      if (transport === transportRef.current) { setSearchResults(results); setStatus('Search complete') }
    } catch (error) { if (transport === transportRef.current) setStatus(errorText(error)) }
  }

  const openConversation = async () => {
    const transport = transportRef.current, user = selectedUser, account = session
    if (!transport || !user || !account || opening.current) return
    opening.current = true; setSecure(false); setStatus('Opening chat...')
    try {
      const id = await transport.createDirectConversation(user.userId)
      if (transport === transportRef.current) await activateRoom(transport, id, user, account.userId)
    } catch (error) { if (transport === transportRef.current) setStatus(errorText(error)) }
    finally { if (transport === transportRef.current) opening.current = false }
  }

  const handleSend = async () => {
    const transport = transportRef.current, active = activeRoom.current
    if (!transport || !active || !secure || !message.trim() || opening.current) return
    const body = message
    setBusy(true)
    try {
      await transport.sendTextMessage(active.id, body, active.recipient.userId)
      if (transport !== transportRef.current || active !== activeRoom.current) return
      setStatus('Encrypted message sent.'); setMessage('')
    } catch (error) {
      if (transport === transportRef.current && active === activeRoom.current) {
        setSecure(false); setStatus(errorText(error))
      }
    } finally { if (transport === transportRef.current) setBusy(false) }
  }

  return (
    <div className="app-shell">
      <header className="topbar"><div><p className="eyebrow">PRESIDIUM</p><h1>{section==='Чаты'?'Private conversations':section}</h1></div></header>
      {session&&<nav className="product-nav" aria-label="Разделы Presidium">{(['Чаты','Лента','Пространства','Комиссионка','Capsule','Профиль'] as const).map(tab=><button key={tab} aria-current={section===tab?'page':undefined} onClick={()=>setSection(tab)}>{tab}</button>)}</nav>}
      <main className="layout" hidden={section!=='Чаты'}>
        <section className="panel">
          <h2>Account</h2>
          <label>Server URL<input value={homeserverUrl} onChange={(e) => setHomeserverUrl(e.target.value)} disabled={busy} /></label>
          <label>Username<input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
          <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          <button type="button" onClick={() => void handleLogin()} disabled={busy}>Sign in</button>
          {!session&&<><button type="button" onClick={()=>void handleRegister()} disabled={busy}>Создать аккаунт</button><p className="muted">Server URL — адрес Matrix-сервера. Здесь можно зарегистрироваться и войти; отдельного входа для других разделов нет.</p></>}
          {session && <><div className="session-box" data-testid="authenticated-state"><strong>{session.username[0].toUpperCase()}{session.username.slice(1)} logged in</strong></div>
            <button type="button" onClick={() => void handleLogout()}>Sign out</button>
            <div className="new-chat"><h2>Search / New Chat</h2>
              <label>Search people<input value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void handleSearch()} /></label>
              <button type="button" onClick={() => void handleSearch()}>Search</button>
              <div className="search-results" aria-label="People search results">{searchResults.map((user) =>
                <button type="button" key={user.userId} onClick={() => setSelectedUser(user)}>{user.displayName || user.userId.split(':')[0].slice(1)}</button>)}</div>
              {selectedUser && <button type="button" onClick={() => void openConversation()}>Open chat</button>}
            </div></>}
        </section>
        <section className="panel chat-panel" data-testid="conversation" data-room-id={roomId}>
          <h2>{activeRecipient?.displayName || activeRecipient?.userId || 'Conversation'}</h2>
          {roomId && <div className="security-indicator" role="status">{secure ? 'Encrypted' : 'Security check required'}</div>}
          <label htmlFor="message-input">Message</label>
          <textarea id="message-input" rows={4} value={message} onChange={(e) => setMessage(e.target.value)} disabled={!roomId || !secure} />
          <button type="button" onClick={() => void handleSend()} disabled={!roomId || !secure || busy || !message.trim()}>Send</button>
          <div className="status-box">{status}</div>
          <div className="message-list">{events.filter((entry) => entry.roomId === roomId).map((entry) =>
            <div key={entry.eventId} data-event-id={entry.eventId} data-room-id={entry.roomId}><span>{entry.sender === session?.userId ? 'You' : entry.sender.split(':')[0].slice(1)}: </span><span>{entry.body}</span></div>)}</div>
        </section>
      </main>
      {session&&section!=='Чаты'&&<FeatureBoundary key={session.userId} onBack={()=>setSection('Чаты')}><Workspace key={session.userId} tab={section} userId={session.userId} token={session.accessToken} transport={session.transport} openChat={id=>{setSection('Чаты');const transport=transportRef.current;if(transport)void transport.createDirectConversation(id).then(room=>activateRoom(transport,room,{userId:id},session.userId)).catch(error=>setStatus(errorText(error)))}}/></FeatureBoundary>}
    </div>
  )
}
export default App
