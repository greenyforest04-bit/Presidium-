import * as sdk from 'matrix-js-sdk'

import type { MessageEvent, MessagingTransport, UserSearchResult } from './MessagingTransport'

export interface MatrixSession {
  userId: string
  deviceId: string
  accessToken: string
  deviceKeys?: NonNullable<Parameters<sdk.MatrixClient["uploadKeysRequest"]>[0]["device_keys"]>
  cryptoDatabasePrefix?: string
}

export class MatrixTransport implements MessagingTransport {
  private client: sdk.MatrixClient | null = null
  private generation = 0
  private allowLegacyRooms = false
  private readonly homeserverUrl: string

  constructor(homeserverUrl = 'http://localhost:8008') {
    this.homeserverUrl = new URL(homeserverUrl).href.replace(/\/$/, '')
  }

  async start(): Promise<void> {
    if (!this.client) {
      throw new Error('Matrix client is not initialized. Call login() first.')
    }

    await this.client.startClient({ initialSyncLimit: 20 })
  }

  connectionPhase(): 'online' | 'offline' | 'expired' {
    const state = this.client?.getSyncState()
    return state === sdk.SyncState.Error || state === sdk.SyncState.Reconnecting || state === sdk.SyncState.Catchup ? 'offline'
      : state === sdk.SyncState.Stopped || !this.client ? 'expired' : 'online'
  }

  async stop(): Promise<void> {
    this.generation++
    this.allowLegacyRooms = false
    this.client?.removeListener(sdk.RoomEvent.MyMembership, this.onMembership)
    this.client?.stopClient()
    this.client = null
  }

  private sessionKey(username: string): string {
    return `presidium_matrix_session_${encodeURIComponent(this.homeserverUrl)}_${encodeURIComponent(username)}`
  }

  async login(username: string, password: string, provided?: MatrixSession): Promise<MatrixSession> {
    void this.stop()
    const generation = this.generation
    const assertCurrent = () => {
      if (generation !== this.generation) throw new Error('Sign-in was cancelled.')
    }
    const storage = this.getSessionStorage()
    const readSession = (key: string): MatrixSession | null => {
      const value = storage?.getItem(key)
      if (!value) return null
      try {
        const saved = JSON.parse(value) as MatrixSession
        if (saved.userId && saved.deviceId && saved.accessToken) return saved
      } catch { /* A malformed cache must not prevent password authentication. */ }
      storage?.removeItem(key)
      return null
    }
    let session: MatrixSession
    if (provided) {
      session = provided
      // Preserve installed E2EE stores when migrating credentials out of localStorage.
      for (const key of Object.keys(storage ?? {})) {
        if (!key.startsWith('presidium_matrix_session_')) continue
        try {
          const old = JSON.parse(storage!.getItem(key)!) as MatrixSession
          if (old.userId !== session.userId || old.deviceId !== session.deviceId) continue
          session.deviceKeys ??= old.deviceKeys
          const tail = key.slice('presidium_matrix_session_'.length)
          const suffix = [session.userId.slice(1).split(':')[0], session.userId].map(name=>'_'+encodeURIComponent(name)).find(name=>tail.endsWith(name))
          const oldServer = suffix ? decodeURIComponent(tail.slice(0, -suffix.length)) : ''
          session.cryptoDatabasePrefix ??= old.cryptoDatabasePrefix ?? (oldServer.startsWith('http')
            ? `presidium_${encodeURIComponent(oldServer)}_${encodeURIComponent(session.userId)}_${encodeURIComponent(session.deviceId)}`
            : `presidium_${session.userId.toLowerCase().replace(/[^a-z0-9]/g, '_')}`)
        } catch { /* Malformed metadata cannot select another user's store. */ }
      }
    } else if (!password) {
      const saved = readSession(this.sessionKey(username))
      if (!saved) throw new Error('Please sign in again.')
      session = saved
    } else {
      const provisional = sdk.createClient({ baseUrl: this.homeserverUrl })
      let saved = readSession(this.sessionKey(username))
      // The old client only had a default localhost server. Migrate its crypto
      // store after password authentication, never by sending its unscoped token.
      const legacy = this.homeserverUrl === 'http://localhost:8008'
        ? readSession(`presidium_matrix_session_${username.toLowerCase()}`) : null
      saved ??= legacy
      const response = await provisional.login('m.login.password', {
        identifier: { type: 'm.id.user', user: username }, password, device_id: saved?.deviceId,
      })
      assertCurrent()
      session = { userId: response.user_id, deviceId: response.device_id, accessToken: response.access_token }
      if (saved?.userId === session.userId && saved.deviceId === session.deviceId) {
        session.cryptoDatabasePrefix = saved.cryptoDatabasePrefix
        if (saved === legacy) {
          session.cryptoDatabasePrefix = `presidium_${session.userId.toLowerCase().replace(/[^a-z0-9]/g, '_')}`
        }
      }
    }
    const client = sdk.createClient({
      baseUrl: this.homeserverUrl, timelineSupport: true,
      accessToken: session.accessToken, userId: session.userId, deviceId: session.deviceId,
    })
    this.client = client
    try {
      const identity = await client.whoami()
      assertCurrent()
      if (identity.user_id !== session.userId || identity.device_id !== session.deviceId) {
        throw new Error('Saved session identity does not match the server.')
      }
      session.cryptoDatabasePrefix ??= `presidium_${encodeURIComponent(this.homeserverUrl)}_${encodeURIComponent(session.userId)}_${encodeURIComponent(session.deviceId)}`
      await client.initRustCrypto({cryptoDatabasePrefix: session.cryptoDatabasePrefix})
      if (provided && session.deviceKeys) {
        // Matrix logout removes server-side device keys. Re-publish the original
        // signed PUBLIC object, only after Rust confirms the installed identity.
        // No private key extraction, new signatures or trust overrides.
        const own = await client.getCrypto()!.getOwnDeviceKeys(), keys = session.deviceKeys
        if (keys.user_id !== session.userId || keys.device_id !== session.deviceId ||
            keys.keys[`ed25519:${session.deviceId}`] !== own.ed25519 ||
            keys.keys[`curve25519:${session.deviceId}`] !== own.curve25519) {
          throw new Error('Сохранённые публичные ключи не соответствуют устройству. Данные сохранены; вход остановлен.')
        }
        await client.uploadKeysRequest({device_keys: keys})
      }

      assertCurrent()
      this.allowLegacyRooms = session.cryptoDatabasePrefix === `presidium_${session.userId.toLowerCase().replace(/[^a-z0-9]/g, '_')}`
      client.on(sdk.RoomEvent.MyMembership, this.onMembership)
      await client.startClient({ initialSyncLimit: 50 })
      await this.waitForInitialSync()
      assertCurrent()
      if (this.allowLegacyRooms) {
        // The installed legacy client did not always write m.direct. Restore
        // those mappings only after the existing strict two-person/E2EE check.
        for (const room of client.getRooms().filter(r=>r.getMyMembership()==='join')) {
          const peer=room.getMembers().find(m=>m.userId!==session.userId&&m.membership==='join')?.userId
          if (!peer) continue
          try { await this.assertDirectConversation(room.roomId,peer);await this.rememberDirectRoom(peer,room.roomId) } catch { /* A group/plaintext room never becomes a direct chat. */ }
        }
      }

      await Promise.all(client.getRooms().filter((room) => room.getMyMembership() === 'invite')
        .map((room) => this.acceptDirectInvite(room)))
      assertCurrent()
      if (provided) {
        for (let attempt = 0; attempt < 20; attempt++) {
          const published = (await client.downloadKeysForUsers([session.userId])).device_keys?.[session.userId]?.[session.deviceId]
          if (published?.signatures) { session.deviceKeys = {...published,signatures:published.signatures}; break }
          await new Promise(resolve=>setTimeout(resolve,200))
        }
        if (!session.deviceKeys) throw new Error('Сервер ещё не подтвердил публичные ключи устройства. Повторите вход.')
      }
      storage?.setItem(this.sessionKey(username), JSON.stringify(provided ? {...session, accessToken: undefined} : session))
      if (this.allowLegacyRooms) storage?.removeItem(`presidium_matrix_session_${username.toLowerCase()}`)
      return session
    } catch (error) {
      if (this.client === client) await this.stop()
      else client.stopClient()
      throw error
    }
  }

  async logout(username: string): Promise<void> {
    const client = this.client
    try {
      await client?.logout()
    } finally {
      this.getSessionStorage()?.removeItem(this.sessionKey(username))
      await this.stop()
    }
  }

  private onMembership = (room: sdk.Room, membership: string): void => {
    if (membership === 'invite') void this.acceptDirectInvite(room).catch(() => undefined)
  }

  private async acceptDirectInvite(room: sdk.Room): Promise<void> {
    const inviter = room.getDMInviter()
    if (!inviter || room.currentState.getStateEvents(sdk.EventType.RoomEncryption, '')
      ?.getContent().algorithm !== 'm.megolm.v1.aes-sha2') return
    const client = this.requireClient()
    await client.joinRoom(room.roomId)
    await this.assertDirectConversation(room.roomId, inviter)
    await this.rememberDirectRoom(inviter, room.roomId)
  }

  private async rememberDirectRoom(userId: string, roomId: string): Promise<void> {
    const client = this.requireClient()
    const direct = client.getAccountData(sdk.EventType.Direct)?.getContent() ?? {}
    await client.setAccountData(sdk.EventType.Direct, {
      ...direct, [userId]: [...new Set([...(direct[userId] ?? []), roomId])],
    })
  }

  async createDirectConversation(userId: string): Promise<string> {
    const client = this.requireClient()
    if (userId === client.getUserId()) throw new Error('Choose another person.')
    const direct = client.getAccountData(sdk.EventType.Direct)?.getContent() ?? {}
    const candidates = client.getRooms().filter((room) =>
      this.allowLegacyRooms || (direct[userId] ?? []).includes(room.roomId) || room.getDMInviter() === userId,
    ).sort((a, b) => b.getLastActiveTimestamp() - a.getLastActiveTimestamp())
    for (const room of candidates) {
      if (!['join', 'invite'].includes(room.getMyMembership())) continue
      try {
        if (room.getMyMembership() === 'invite') await client.joinRoom(room.roomId)
        await this.assertDirectConversation(room.roomId, userId)
        await this.rememberDirectRoom(userId, room.roomId)
        return room.roomId
      } catch {
        // A stale direct-room mapping must never authorize a group or plaintext room.
      }
    }
    const result = await client.createRoom({
      invite: [userId], is_direct: true, preset: sdk.Preset.TrustedPrivateChat,
      initial_state: [{ type: 'm.room.encryption', content: {
        algorithm: 'm.megolm.v1.aes-sha2', rotation_period_ms: 86_400_000, rotation_period_msgs: 100,
      } }],
    })
    await this.assertDirectConversation(result.room_id, userId)
    await this.rememberDirectRoom(userId, result.room_id)
    return result.room_id
  }

  async assertDirectConversation(roomId: string, userId: string): Promise<void> {
    const client = this.requireClient()
    const state = await client.roomState(roomId)
    const encryption = state.find((event) => event.type === 'm.room.encryption' && event.state_key === '')
    const allMembers = state.filter((event) => event.type === 'm.room.member')
    const members = allMembers.filter((event) =>
      typeof event.content.membership === 'string' && ['join', 'invite'].includes(event.content.membership))
    const ids = new Set(members.map((event) => event.state_key))
    if (state.some((event) => event.type === 'm.presidium.kind' && event.content.kind === 'group') ||
      encryption?.content.algorithm !== 'm.megolm.v1.aes-sha2' || ids.size !== 2 ||
      allMembers.some((event) => ![client.getUserId(), userId].includes(event.state_key)) ||
      !ids.has(client.getUserId()!) || !ids.has(userId)) {
      throw new Error('This room is not an encrypted conversation between you and this person.')
    }
  }

  listGroupConversations(): {id:string;name:string;invited:boolean}[] {
    return this.requireClient().getRooms().filter(room =>
      room.getMyMembership() === 'join'
        ? room.currentState.getStateEvents('m.presidium.kind', '')?.getContent().kind === 'group'
        : room.getMyMembership() === 'invite' && !room.getDMInviter(),
    ).map(room => ({id:room.roomId,name:room.name,invited:room.getMyMembership()==='invite'}))
  }

  listConversations() {
    const client=this.requireClient(),direct=client.getAccountData(sdk.EventType.Direct)?.getContent()??{}
    const directRooms=new Set(Object.values(direct).flatMap(ids=>Array.isArray(ids)?ids:[]))
    return client.getRooms().filter(room=>['join','invite'].includes(room.getMyMembership())).map(room=>{
      const group=room.currentState.getStateEvents('m.presidium.kind','')?.getContent().kind==='group'
      const member=room.getMembers().find(m=>m.userId!==client.getUserId()&&['join','invite'].includes(m.membership??''))
      return {id:room.roomId,name:room.name,membership:room.getMyMembership(),encrypted:room.hasEncryptionStateEvent(),
        kind:(!group&&(directRooms.has(room.roomId)||room.getDMInviter())?'chat':'group') as 'chat'|'group',
        recipient:member?.userId,unread:room.getUnreadNotificationCount(),lastTime:room.getLastActiveTimestamp()}
    })
  }

  async loadHistory(roomId:string):Promise<MessageEvent[]> {
    const client=this.requireClient(),room=client.getRoom(roomId)
    if(room)await client.scrollback(room,50)
    return this.getMessages(roomId)
  }

  async setDisplayName(name:string){await this.requireClient().setDisplayName(name)}

  async deviceSecurity() {
    const client=this.requireClient(),crypto=client.getCrypto()
    if(!crypto)throw new Error('Crypto is not initialized')
    const status=await crypto.getDeviceVerificationStatus(client.getUserId()!,client.getDeviceId()!)
    return {...await crypto.getOwnDeviceKeys(),deviceId:client.getDeviceId(),verified:status?.isVerified()??false,signed:status?.signedByOwner??false}
  }

  async deviceKey() { return this.requireClient().getCrypto()?.getOwnDeviceKeys() }

  async createGroupConversation(name:string, users:string[]):Promise<string> {
    if (!name.trim() || users.length === 0 || users.length > 30 || users.some(id=>!/^@[^:]+:.+$/.test(id))) {
      throw new Error('Укажите название и от 1 до 30 полных Matrix ID участников.')
    }
    const result = await this.requireClient().createRoom({name:name.trim(),invite:[...new Set(users)],
      preset:sdk.Preset.PrivateChat, initial_state:[
        {type:'m.presidium.kind',state_key:'',content:{kind:'group'}},
        {type:'m.room.encryption',state_key:'',content:{algorithm:'m.megolm.v1.aes-sha2',rotation_period_ms:86_400_000,rotation_period_msgs:100}},
      ]})
    await this.assertGroupConversation(result.room_id)
    return result.room_id
  }

  async assertGroupConversation(roomId:string):Promise<void> {
    const client=this.requireClient(),state=await client.roomState(roomId)
    if (!state.some(e=>e.type==='m.presidium.kind' && e.content.kind==='group') ||
      !state.some(e=>e.type==='m.room.encryption' && e.content.algorithm==='m.megolm.v1.aes-sha2') ||
      !state.some(e=>e.type==='m.room.member' && e.state_key===client.getUserId() && e.content.membership==='join')) {
      throw new Error('Это не доступная вам зашифрованная группа Presidium.')
    }
  }

  async joinGroupConversation(roomId:string):Promise<void> {
    await this.requireClient().joinRoom(roomId)
    await this.assertGroupConversation(roomId)
  }

  async sendGroupMessage(roomId:string,body:string):Promise<string> {
    const client=this.requireClient()
    await this.assertGroupConversation(roomId)
    if (!client.getCrypto() || !await client.getCrypto()!.isEncryptionEnabledInRoom(roomId)) {
      throw new Error('Шифрование группы ещё не готово. Повторите отправку.')
    }
    return (await client.sendMessage(roomId,{msgtype:sdk.MsgType.Text,body})).event_id
  }

  async searchUsers(term: string): Promise<UserSearchResult[]> {
    const client = this.requireClient()
    const response = await client.searchUserDirectory({ term: term.trim(), limit: 10 })
    const currentUserId = client.getUserId()

    const results = response.results
      .filter((result) => result.user_id !== currentUserId)
      .map((result) => ({ userId: result.user_id, displayName: result.display_name }))
    const query = term.trim()
    const exactId = query.startsWith('@') ? query : `@${query}:${currentUserId?.slice(currentUserId.indexOf(':') + 1)}`
    if (exactId !== currentUserId && !results.some((result) => result.userId === exactId)) {
      try {
        const profile = await client.getProfileInfo(exactId)
        results.push({ userId: exactId, displayName: profile.displayname })
      } catch (error) {
        if (!(error instanceof sdk.MatrixError) || !['M_NOT_FOUND', 'M_INVALID_PARAM'].includes(error.errcode ?? '')) throw error
      }
    }
    return results
  }

  async sendTextMessage(roomId: string, body: string, recipient?: string): Promise<string> {
    const client = this.requireClient()
    if (!recipient) throw new Error('A recipient is required for a private message.')
    await this.assertDirectConversation(roomId, recipient)
    const crypto = client.getCrypto()
    if (!crypto || !await crypto.isEncryptionEnabledInRoom(roomId)) {
      throw new Error('Encryption is not ready. Message was not sent.')
    }
    const event = await client.sendMessage(roomId, {
      msgtype: sdk.MsgType.Text,
      body,
    })

    return event.event_id
  }

  subscribeToMessages(roomId: string | null, callback: (event: MessageEvent) => void): () => void {
    const client = this.requireClient()
    const emittedEventIds = new Set<string>()
    const pendingEvents = new Set<sdk.MatrixEvent>()
    const pendingLocalEvents = new Set<sdk.MatrixEvent>()

    const onDecrypted = (event: sdk.MatrixEvent) => {
      pendingEvents.delete(event)
      emitMessage(event)
    }

    const onLocalEventIdReplaced = (event: sdk.MatrixEvent) => {
      pendingLocalEvents.delete(event)
      emitMessage(event)
    }

    const emitMessage = (event: sdk.MatrixEvent) => {
      const eventRoomId = event.getRoomId()
      if ((roomId && eventRoomId !== roomId) || !eventRoomId || event.getType() !== 'm.room.message') {
        return
      }

      const eventId = event.getId()
      if (!eventId || emittedEventIds.has(eventId) || event.isDecryptionFailure()) {
        return
      }

      if (eventId.startsWith('~')) {
        if (!pendingLocalEvents.has(event)) {
          pendingLocalEvents.add(event)
          event.once(sdk.MatrixEventEvent.LocalEventIdReplaced, onLocalEventIdReplaced)
        }
        return
      }

      const body = event.getContent()?.body
      if (typeof body !== 'string') {
        return
      }

      emittedEventIds.add(eventId)
      callback({
        roomId: eventRoomId,
        sender: event.getSender() ?? 'unknown',
        body,
        eventId,
        timestamp: event.getTs() ?? Date.now(),
      })
    }

    const onTimeline = (event: sdk.MatrixEvent) => {
      if (event.getRoomId() !== roomId) {
        if (roomId !== null) {
          return
        }
      }

      if (event.getType() === 'm.room.encrypted') {
        pendingEvents.add(event)
        event.once(sdk.MatrixEventEvent.Decrypted, onDecrypted)
        emitMessage(event)
        return
      }

      if (event.getType() !== 'm.room.message') {
        return
      }

      emitMessage(event)
    }

    client.on(sdk.RoomEvent.Timeline, onTimeline)
    for (const room of client.getRooms()) {
      if (roomId && room.roomId !== roomId) continue
      for (const event of room.getLiveTimeline().getEvents()) onTimeline(event)
    }

    return () => {
      client.removeListener(sdk.RoomEvent.Timeline, onTimeline)
      pendingEvents.forEach((event) => event.removeListener(sdk.MatrixEventEvent.Decrypted, onDecrypted))
      pendingLocalEvents.forEach((event) =>
        event.removeListener(sdk.MatrixEventEvent.LocalEventIdReplaced, onLocalEventIdReplaced),
      )
      pendingEvents.clear()
      pendingLocalEvents.clear()
    }
  }

  subscribeToRoomChanges(roomId: string, callback: () => void): () => void {
    const client = this.requireClient()
    const listener = (event: sdk.MatrixEvent) => {
      if (event.getRoomId() === roomId && ['m.room.member', 'm.room.encryption'].includes(event.getType())) callback()
    }
    client.on(sdk.RoomStateEvent.Events, listener)
    return () => { client.removeListener(sdk.RoomStateEvent.Events, listener) }
  }

  async getMessages(roomId: string): Promise<MessageEvent[]> {
    const client = this.requireClient()
    const room = client.getRoom(roomId)
    const events = room?.getLiveTimeline().getEvents() ?? []
    await Promise.all(events.filter((event) => event.isEncrypted())
      .map((event) => client.decryptEventIfNeeded(event)))

    return events
      .filter((event) => event.getType() === 'm.room.message')
      .filter((event) => !!event.getId() && !event.getId()!.startsWith('~'))
      .filter((event) => !event.isDecryptionFailure())
      .map((event) => ({
        roomId,
        sender: event.getSender() ?? 'unknown',
        body: event.getContent()?.body ?? '',
        eventId: event.getId() ?? `${Date.now()}`,
        timestamp: event.getTs() ?? Date.now(),
      }))
  }

  async markRead(roomId: string, eventId: string): Promise<void> {
    const client = this.requireClient()
    await client.setRoomReadMarkers(roomId, eventId)
  }

  async setTyping(roomId: string, isTyping: boolean): Promise<void> {
    const client = this.requireClient()
    await client.sendTyping(roomId, isTyping, 1000)
  }

  private async waitForInitialSync(): Promise<void> {
    const client = this.requireClient()
    if (client.getSyncState() === sdk.SyncState.Prepared || client.getSyncState() === sdk.SyncState.Syncing) return
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => { clearTimeout(timer); client.removeListener(sdk.ClientEvent.Sync, onSync) }
      const onSync = (state: sdk.SyncState, _previous: sdk.SyncState | null, data?: sdk.SyncStateData) => {
        if (state === sdk.SyncState.Prepared || state === sdk.SyncState.Syncing) { cleanup(); resolve() }
        else if (state === sdk.SyncState.Error) { cleanup(); reject(data?.error ?? new Error('Sync failed.')) }
      }
      const timer = setTimeout(() => { cleanup(); reject(new Error('Initial sync timed out.')) }, 30_000)
      client.on(sdk.ClientEvent.Sync, onSync)
    })
  }

  private getSessionStorage(): Storage | null {
    if (typeof window === 'undefined') {
      return null
    }

    return window.localStorage
  }

  private requireClient(): sdk.MatrixClient {
    if (!this.client) {
      throw new Error('Matrix client is not initialized.')
    }

    return this.client
  }
}
