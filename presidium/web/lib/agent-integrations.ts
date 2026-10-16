import type {AgentItem,AgentScope} from './presidium-agent';
/** Adapter boundary. A connector must report a connection only after validating its authorization. */
export type ConnectionState='unconfigured'|'disconnected'|'authorizing'|'connected'|'expired'|'error';
export type ConnectorSession={id:string;status:ConnectionState;permissions:readonly string[];reason?:string};
export interface AgentConnectorAdapter{
 id:string;
 session(signal:AbortSignal):Promise<ConnectorSession>;
 connect(permissions:readonly string[],signal:AbortSignal):Promise<{authorizationURL:string}>;
 collect(period:{from:number;to:number},signal:AbortSignal):Promise<AgentItem[]>;
 revoke(signal:AbortSignal):Promise<void>;
}
/** Tokens belong to an encrypted server vault, never to an adapter's client-side session object. */
export class IntegrationRegistry{
 private adapters=new Map<string,AgentConnectorAdapter>();
 register(adapter:AgentConnectorAdapter){if(this.adapters.has(adapter.id))throw Error('Коннектор уже зарегистрирован.');this.adapters.set(adapter.id,adapter)}
 get(id:string){const adapter=this.adapters.get(id);if(!adapter)throw Error('Коннектор ещё не подключён.');return adapter}
 async available(signal:AbortSignal){return Promise.all([...this.adapters.values()].map(a=>a.session(signal)))}
 async collect(id:string,permission:string,period:{from:number;to:number},signal:AbortSignal){const adapter=this.get(id),session=await adapter.session(signal);signal.throwIfAborted();if(session.status!=='connected'||!session.permissions.includes(permission))throw Error(session.reason||'Нет авторизованного доступа к источнику.');const items=await adapter.collect(period,signal);signal.throwIfAborted();return items}
 revoke(id:string,signal:AbortSignal){return this.get(id).revoke(signal)}
}
export type LocalSourceAuthorization={scope:AgentScope;granted:boolean};
// Deliberately empty: registering metadata is not OAuth authorization.
export const integrations=new IntegrationRegistry();
