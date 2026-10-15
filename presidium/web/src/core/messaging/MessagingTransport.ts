export interface MessageEvent {
  roomId: string;
  sender: string;
  body: string;
  eventId: string;
  timestamp: number;
}

export interface UserSearchResult {
  userId: string;
  displayName?: string;
}

export interface MessagingTransport {
  start(): Promise<void>;
  stop(): Promise<void>;
  login(username: string, password: string): Promise<{ userId: string; deviceId: string; accessToken: string }>;
  searchUsers(term: string): Promise<UserSearchResult[]>;
  createDirectConversation(userId: string): Promise<string>;
  sendTextMessage(roomId: string, body: string): Promise<string>;
  subscribeToMessages(roomId: string | null, callback: (event: MessageEvent) => void): () => void;
  getMessages(roomId: string): Promise<MessageEvent[]>;
  markRead(roomId: string, eventId: string): Promise<void>;
  setTyping(roomId: string, isTyping: boolean): Promise<void>;
}
