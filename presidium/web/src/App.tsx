import { useEffect, useRef, useState } from 'react'

import { MatrixTransport } from './core/messaging/MatrixTransport'
import type { MessageEvent, UserSearchResult } from './core/messaging/MessagingTransport'

function App() {
  const transportRef = useRef<MatrixTransport | null>(null)
  const unsubscribeRef = useRef<(() => void) | null>(null)

  const [homeserverUrl, setHomeserverUrl] = useState(
    import.meta.env.VITE_MATRIX_HOMESERVER_URL ?? 'http://localhost:8008',
  )
  const [username, setUsername] = useState('alice')
  const [password, setPassword] = useState('alice-password')
  const [searchTerm, setSearchTerm] = useState('')
  const [searchResults, setSearchResults] = useState<UserSearchResult[]>([])
  const [selectedUser, setSelectedUser] = useState<UserSearchResult | null>(null)
  const [roomId, setRoomId] = useState('')
  const [message, setMessage] = useState('PRESIDIUM_E2EE_SECRET_739184')
  const [status, setStatus] = useState('Ready')
  const [session, setSession] = useState<{ userId: string; deviceId: string } | null>(null)
  const [events, setEvents] = useState<MessageEvent[]>([])

  const addMessage = (event: MessageEvent) => {
    setEvents((previous) =>
      previous.some((existing) => existing.eventId === event.eventId)
        ? previous
        : [...previous, event],
    )
  }

  const listenForMessages = (transport: MatrixTransport, currentUserId: string) => {
    unsubscribeRef.current?.()
    unsubscribeRef.current = transport.subscribeToMessages(null, (event) => {
      addMessage(event)
      setRoomId((currentRoomId) => currentRoomId || event.roomId)

      if (event.sender !== currentUserId) {
        const incomingUser = {
          userId: event.sender,
          displayName: event.sender.split(':')[0].slice(1),
        }
        setSelectedUser((currentUser) => currentUser ?? incomingUser)
        window.localStorage.setItem('presidium_active_recipient', event.sender)
      }
    })
  }

  const openConversation = async (user: UserSearchResult, transport = transportRef.current) => {
    if (!transport) {
      return
    }

    setStatus(`Opening chat with ${user.displayName || user.userId.split(':')[0].slice(1)}...`)
    const directRoom = await transport.createDirectConversation(user.userId)
    setSelectedUser(user)
    setRoomId(directRoom)
    window.localStorage.setItem('presidium_active_recipient', user.userId)

    const history = await transport.getMessages(directRoom)
    setEvents((previous) => {
      const byEventId = new Map(previous.map((event) => [event.eventId, event]))
      history.forEach((event) => byEventId.set(event.eventId, event))
      return [...byEventId.values()].sort((left, right) => left.timestamp - right.timestamp)
    })
    setStatus('Chat is encrypted')
  }

  useEffect(() => {
    const savedUsername = window.localStorage.getItem('presidium_active_user')
    if (!savedUsername) {
      return
    }

    const restoreSession = async () => {
      const transport = new MatrixTransport(homeserverUrl)
      const result = await transport.login(savedUsername, '')
      transportRef.current = transport
      setUsername(savedUsername)
      setSession({ userId: result.userId, deviceId: result.deviceId })
      setStatus(`${savedUsername[0].toUpperCase()}${savedUsername.slice(1)} logged in`)
      listenForMessages(transport, result.userId)

      const savedRecipient = window.localStorage.getItem('presidium_active_recipient')
      if (savedRecipient) {
        await openConversation(
          { userId: savedRecipient, displayName: savedRecipient.split(':')[0].slice(1) },
          transport,
        )
      }
    }

    void restoreSession().catch((error: unknown) => {
      window.localStorage.removeItem('presidium_active_user')
      setStatus(error instanceof Error ? error.message : 'Session restore failed')
    })
  }, [homeserverUrl])

  const handleLogin = async () => {
    try {
      setStatus('Signing in securely...')
      const transport = new MatrixTransport(homeserverUrl)
      const result = await transport.login(username, password)
      transportRef.current = transport
      setSession({ userId: result.userId, deviceId: result.deviceId })
      window.localStorage.setItem('presidium_active_user', username)
      listenForMessages(transport, result.userId)
      setStatus(`${username[0].toUpperCase()}${username.slice(1)} logged in`)
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Login failed')
    }
  }

  const handleSearch = async () => {
    if (!transportRef.current || !searchTerm.trim()) {
      return
    }

    try {
      setStatus('Searching people...')
      setSearchResults(await transportRef.current.searchUsers(searchTerm))
      setStatus('Search complete')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Search failed')
    }
  }

  const handleSend = async () => {
    if (!transportRef.current || !roomId) {
      setStatus('Please log in and create a direct room first.')
      return
    }

    try {
      setStatus('Sending encrypted message via Matrix transport...')
      await transportRef.current.sendTextMessage(roomId, message)
      setStatus('Encrypted message sent via Matrix SDK.')
      setMessage('')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Message send failed')
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">PRESIDIUM</p>
          <h1>Private conversations</h1>
        </div>
        <div className="badge">Private by default</div>
      </header>

      <main className="layout">
        <section className="panel">
          <h2>Account</h2>
          <label>
            Server URL
            <input value={homeserverUrl} onChange={(e) => setHomeserverUrl(e.target.value)} />
          </label>
          <label>
            Username
            <input value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <button type="button" onClick={handleLogin}>Sign in</button>

          {session && (
            <div className="session-box" data-testid="authenticated-state">
              <strong>{username[0].toUpperCase()}{username.slice(1)} logged in</strong>
            </div>
          )}

          {session && (
            <div className="new-chat">
              <h2>Search / New Chat</h2>
              <label>
                Search people
                <input
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  onKeyDown={(event) => event.key === 'Enter' && void handleSearch()}
                />
              </label>
              <button type="button" onClick={handleSearch}>Search</button>
              {searchResults.length > 0 && (
                <div className="search-results" aria-label="People search results">
                  {searchResults.map((user) => {
                    const displayName = user.displayName || user.userId.split(':')[0].slice(1)
                    return (
                      <button
                        className={selectedUser?.userId === user.userId ? 'search-result selected' : 'search-result'}
                        type="button"
                        key={user.userId}
                        onClick={() => setSelectedUser(user)}
                      >
                        {displayName}
                      </button>
                    )
                  })}
                </div>
              )}
              {selectedUser && (
                <button type="button" onClick={() => void openConversation(selectedUser)}>
                  Open chat
                </button>
              )}
            </div>
          )}
        </section>

        <section className="panel chat-panel" data-testid="conversation" data-room-id={roomId}>
          <h2>Conversation</h2>
          {roomId && <div className="security-indicator" role="status">Encrypted</div>}
          <label>
            Message
            <textarea
              rows={4}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              disabled={!roomId}
            />
          </label>
          <button type="button" onClick={handleSend} disabled={!roomId}>Send</button>
          <div className="status-box">{status}</div>

          <div className="message-list">
            {events.length === 0 ? (
              <p className="empty">No messages yet.</p>
            ) : (
              events.map((entry) => (
                <div key={entry.eventId} data-event-id={entry.eventId} data-room-id={entry.roomId}>
                  <span>{entry.sender === session?.userId ? 'You' : selectedUser?.displayName || 'Contact'}: </span>
                  <span>{entry.body}</span>
                </div>
              ))
            )}
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
