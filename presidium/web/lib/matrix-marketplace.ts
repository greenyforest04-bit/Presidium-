// Public listing metadata only. This context is never sent as a chat message automatically.
export type MarketConversationContext={roomId:string;listing:{id:string;title:string;priceKopecks:number;image?:string};onOpenListing:()=>void};
