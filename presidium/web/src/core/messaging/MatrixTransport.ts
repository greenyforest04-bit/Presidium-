import * as sdk from 'matrix-js-sdk'

import type { MessageEvent, MessagingTransport, UserSearchResult } from './MessagingTransport'

export interface MatrixSession {
  userId: string
  deviceId: string
  accessToken: string
}

export class MatrixTransport implements MessagingTransport {
  private client: sdk.MatrixClient | null = null
  private readonly homeserverUrl: string

  constructor(homeserverUrl = 'http://localhost:8008') {
    this.homeserverUrl = homeserverUrl
  }

  async start(): Promise<void> {
    if (!this.client) {
      throw new Error('Matrix client is not initialized. Call login() first.')
    }

    await this.client.startClient({ initialSyncLimit: 20 })
  }

  async stop(): Promise<void> {
    this.client?.stopClient()
    this.client = null
  }

  async login(username: string, password: string): Promise<MatrixSession> {
    const storage = this.getSessionStorage()
    const sessionKey = `presidium_matrix_session_${username.toLowerCase()}`
    const persisted = storage?.getItem(sessionKey)

    if (persisted) {
      try {
        const stored = JSON.parse(persisted) as Partial<MatrixSession>
        if (stored.userId && stored.deviceId && stored.accessToken) {
          const client = sdk.createClient({
            baseUrl: this.homeserverUrl,
            timelineSupport: true,
            accessToken: stored.accessToken,
            userId: stored.userId,
            deviceId: stored.deviceId,
          })

          this.client = client
          await client.initRustCrypto({
            cryptoDatabasePrefix: `presidium_${stored.userId.toLowerCase().replace(/[^a-z0-9]/g, '_')}`,
          })
          await client.startClient({ initialSyncLimit: 20 })
          await this.waitForInitialSync()

          return {
            accessToken: stored.accessToken,
            userId: stored.userId,
            deviceId: stored.deviceId,
          }
        }
      } catch {
        storage?.removeItem(sessionKey)
      }
    }

    const provisionalClient = sdk.createClient({
      baseUrl: this.homeserverUrl,
      timelineSupport: true,
    })

    const response = await provisionalClient.loginWithPassword(username, password)

    const client = sdk.createClient({
      baseUrl: this.homeserverUrl,
      timelineSupport: true,
      accessToken: response.access_token,
      userId: response.user_id,
      deviceId: response.device_id,
    })

    this.client = client

    await client.initRustCrypto({
      cryptoDatabasePrefix: `presidium_${response.user_id.toLowerCase().replace(/[^a-z0-9]/g, '_')}`,
    })
    await client.startClient({ initialSyncLimit: 20 })
    await this.waitForInitialSync()

    const session = {
      accessToken: response.access_token,
      userId: response.user_id,
      deviceId: response.device_id,
    }

    storage?.setItem(sessionKey, JSON.stringify(session))

    return session
  }

  async createDirectConversation(userId: string): Promise<string> {
    const client = this.requireClient()

    const existingRoomId = await this.waitForExistingDirectConversation(client, userId, 20_000)

    if (existingRoomId) {
      const room = client.getRoom(existingRoomId)
      if (room && room.getMyMembership() === 'invite') {
        await client.joinRoom(existingRoomId)
      }
      return existingRoomId
    }

    const room = await client.createRoom({
      invite: [userId],
      is_direct: true,
      preset: sdk.Preset.TrustedPrivateChat,
      initial_state: [
        {
          type: 'm.room.encryption',
          content: {
            algorithm: 'm.megolm.v1.aes-sha2',
            rotation_period_ms: 86_400_000,
            rotation_period_msgs: 100,
          },
        },
      ],
    })

    await client.sendStateEvent(
      room.room_id,
      sdk.EventType.RoomEncryption,
      {
        algorithm: 'm.megolm.v1.aes-sha2',
        rotation_period_ms: 86_400_000,
        rotation_period_msgs: 100,
      },
      '',
    )

    return room.room_id
  }

  async searchUsers(term: string): Promise<UserSearchResult[]> {
    const client = this.requireClient()
    const response = await client.searchUserDirectory({ term: term.trim(), limit: 10 })
    const currentUserId = client.getUserId()

    return response.results
      .filter((result) => result.user_id !== currentUserId)
      .map((result) => ({
        userId: result.user_id,
        displayName: result.display_name,
      }))
  }

  async sendTextMessage(roomId: string, body: string): Promise<string> {
    const client = this.requireClient()
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

  async getMessages(roomId: string): Promise<MessageEvent[]> {
    const client = this.requireClient()
    const room = client.getRoom(roomId)
    const events = room?.getLiveTimeline().getEvents() ?? []

    return events
      .filter((event) => event.getType() === 'm.room.message')
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
    const deadline = Date.now() + 30_000

    while (Date.now() < deadline) {
      if (this.client?.getRooms().length) {
        return
      }

      await new Promise((resolve) => setTimeout(resolve, 250))
    }
  }

  private async waitForExistingDirectConversation(
    client: sdk.MatrixClient,
    userId: string,
    timeoutMs = 20_000,
  ): Promise<string | null> {
    const deadline = Date.now() + timeoutMs

    while (Date.now() < deadline) {
      const roomId = this.findExistingDirectConversation(client, userId)
      if (roomId) {
        return roomId
      }

      await new Promise((resolve) => setTimeout(resolve, 500))
    }

    return this.findExistingDirectConversation(client, userId)
  }

  private findExistingDirectConversation(
    client: sdk.MatrixClient,
    userId: string,
  ): string | null {
    const matchingRooms = client
      .getRooms()
      .filter((room) => {
        if (!['join', 'invite'].includes(room.getMyMembership())) {
          return false
        }

        return room
          .getMembers()
          .some(
            (member) =>
              member.userId === userId &&
              member.membership &&
              ['join', 'invite'].includes(member.membership),
          )
      })
      .sort((left, right) => {
        const leftTimestamp = left.getLastActiveTimestamp?.() ?? 0
        const rightTimestamp = right.getLastActiveTimestamp?.() ?? 0

        if (leftTimestamp !== rightTimestamp) {
          return rightTimestamp - leftTimestamp
        }

        const leftPriority = left.getMyMembership() === 'join' ? 1 : 0
        const rightPriority = right.getMyMembership() === 'join' ? 1 : 0
        return rightPriority - leftPriority
      })

    return matchingRooms[0]?.roomId ?? null
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
