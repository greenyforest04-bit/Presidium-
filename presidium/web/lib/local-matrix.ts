import {setStorageOwner} from './storage';
import type {MatrixTransport as ExistingTransport,MatrixSession as ExistingSession} from '../src/core/messaging/MatrixTransport';
import type {MessageEvent} from '../src/core/messaging/MessagingTransport';
import type {NetworkMessage,TransportState} from './argon-matrix-contract';

export const unavailable=async():Promise<never>=>{throw Error('Эта функция исходного интерфейса пока не подключена к локальному транспорту. Операция не выполнена.');};
type Session=ExistingSession&{baseUrl:string;username:string;cryptoPrefix:string;storageKey:number[];displayName?:string};
export class LocalMatrix {
  core?:ExistingTransport;
  session?:Session;
  private value:TransportState={phase:'disconnected',rooms:[]};
  private listeners=new Set<(value:TransportState)=>void>();
  private rows=new Map<string,NetworkMessage>(); // UI projection only; history/keys remain with Matrix SDK.
  private drafts=new Map<string,string>();
  private off?:()=>void;
  private timer?:ReturnType<typeof setInterval>;
  private connecting?:Promise<void>;
  snapshot=()=>this.value;
  subscribe=(listener:(value:TransportState)=>void)=>{this.listeners.add(listener);listener(this.value);return()=>{this.listeners.delete(listener)}};
  private emit(patch:Partial<TransportState>={}){this.value={...this.value,...patch};for(const fn of this.listeners)fn(this.value)}
  private ingest(event:MessageEvent){
    if(!event.eventId||event.eventId.startsWith('~'))return;
    const room=this.value.rooms.find(r=>r.id===event.roomId);
    this.rows.set(event.eventId,{id:event.eventId,body:event.body,sender:event.sender,time:event.timestamp,own:event.sender===this.session?.userId,
      status:'accepted',encrypted:!!room?.encrypted,failed:false,delivery:'accepted',readable:true,replyable:false,editable:false,deletable:false,edited:false,editRevision:event.eventId});
    // room association is view metadata, not another message store.
    Object.assign(this.rows.get(event.eventId)!,{roomId:event.roomId});
  }
  private refresh=()=>{
    if(!this.core||!this.session)return;
    const rooms=this.core.listConversations().map(room=>({...room,preview:this.messages(room.id).at(-1)?.body||'Пока нет сообщений'})).sort((a,b)=>b.lastTime-a.lastTime);
    this.emit({rooms,phase:this.core.connectionPhase()});
  };
  async connect(input:{baseUrl:string;username:string;password:string;displayName?:string;register?:boolean}) {
    if(this.connecting)return this.connecting;
    this.connecting=this.login(input);try{await this.connecting}finally{this.connecting=undefined}
  }
  async register(input:{baseUrl:string;username:string;password:string;displayName?:string}){await this.connect({...input,register:true} as Parameters<LocalMatrix['connect']>[0])}
  private async login(input:{baseUrl:string;username:string;password:string;displayName?:string;register?:boolean}){
    this.stop();this.emit({phase:'connecting',error:undefined});
    const {MatrixTransport}=await import('../src/core/messaging/MatrixTransport');
    const baseUrl=new URL(input.baseUrl).href.replace(/\/$/,''),core=new MatrixTransport(baseUrl);this.core=core;
    try{
      let saved:ExistingSession;
      if(input.password){
        const key=`presidium_matrix_session_${encodeURIComponent(baseUrl)}_${encodeURIComponent(input.username.trim().toLowerCase())}`;
        let deviceId:string|undefined;try{deviceId=JSON.parse(localStorage.getItem(key)||'null')?.deviceId}catch{}
        if(!deviceId)for(const oldKey of Object.keys(localStorage)){if(!oldKey.startsWith('presidium_matrix_session_'))continue;try{const old=JSON.parse(localStorage.getItem(oldKey)!);if(old.userId===`@${input.username.trim().toLowerCase()}:presidium.local`){deviceId=old.deviceId;break}}catch{}}
        let attempt:string|undefined;
        if(input.register){const attemptKey='presidium_registration_attempt:'+input.username;attempt=sessionStorage.getItem(attemptKey)||crypto.randomUUID();sessionStorage.setItem(attemptKey,attempt)}
        const value=await authRequest(input.register?'register':'login',{username:input.username,password:input.password,display_name:input.displayName||'',device_id:deviceId,...(attempt?{idempotency_key:attempt}:{})});
        if(input.register)sessionStorage.removeItem('presidium_registration_attempt:'+input.username);
        saved=await core.login(input.username,'',{userId:value.user_id,deviceId:value.device_id,accessToken:value.access_token});
      }else{
        const value=await authRequest('session');
        saved=await core.login(input.username,'',{userId:value.user_id,deviceId:value.device_id,accessToken:value.access_token});
      }
      this.session={...saved,baseUrl,username:input.username,cryptoPrefix:saved.cryptoDatabasePrefix??`presidium_${encodeURIComponent(baseUrl)}_${encodeURIComponent(saved.userId)}_${encodeURIComponent(saved.deviceId)}`,storageKey:[]};
      setStorageOwner(saved.userId);
      localStorage.setItem('presidium_product_owner',saved.userId);
      if(input.password)localStorage.setItem('presidium_auth_changed',String(Date.now()));
      localStorage.setItem('presidium_matrix_active_server',baseUrl);
      localStorage.setItem(`presidium_active_user_${encodeURIComponent(baseUrl)}`,input.username);
      for(const key of Object.keys(localStorage)){if(key.startsWith('presidium_matrix_session_')){try{const old=JSON.parse(localStorage.getItem(key)!);if(old.accessToken){delete old.accessToken;localStorage.setItem(key,JSON.stringify(old))}}catch{}}}
      if(input.register&&input.displayName){
        const account=await fetch('/api/account',{cache:'no-store'}).then(r=>r.json());
        if(account.profile)await fetch('/api/account',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...account.profile,name:input.displayName})});
      }
      const profile=await fetch('/api/account',{cache:'no-store'}).then(r=>r.ok?r.json():null);
      if(profile?.profile)this.session.displayName=profile.profile.name;
      this.emit({phase:'online',session:this.session,outbox:[]});this.refresh();
      this.off=core.subscribeToMessages(null,event=>{this.ingest(event);this.refresh()});
      let checks=0;this.timer=setInterval(()=>{this.refresh();if(++checks%3===0)void this.validateSession()},1500);
      await Promise.all(this.value.rooms.filter(r=>r.membership==='join').map(async r=>{for(const event of await core.getMessages(r.id))this.ingest(event)}));
      this.refresh();
    }catch(error){await core.stop();this.core=undefined;this.session=undefined;this.emit({phase:'disconnected',rooms:[],error:error instanceof Error?error.message:'Ошибка входа'});throw error}
  }
  async validateSession(){try{const value=await authRequest('session');if(this.session&&(value.user_id!==this.session.userId||value.device_id!==this.session.deviceId))this.clearActive()}catch(e){if((e as {status?:number}).status===401)this.clearActive()}}
  async resume(){
    if(this.session)return;
    const value=await authRequest('session');
    const baseUrl=window.location.origin,username=value.user_id.slice(1).split(':')[0];
    await this.connect({baseUrl,username,password:''});
  }
  messages(roomId:string){return [...this.rows.values()].filter(row=>(row as NetworkMessage&{roomId:string}).roomId===roomId).sort((a,b)=>a.time-b.time)}
  async activateRoom(roomId:string,signal?:AbortSignal){if(signal?.aborted)return;for(const event of await this.core!.getMessages(roomId))this.ingest(event);this.refresh()}
  deactivateRoom(){/* View only; the shared transport remains active. */}
  async history(roomId:string){for(const event of await this.core!.loadHistory(roomId))this.ingest(event);this.refresh()}
  historyAvailable(){return true}
  draft(roomId:string,value?:string){if(value!==undefined)this.drafts.set(roomId,value);return this.drafts.get(roomId)||''}
  replyDraft(){return ''}
  async send(roomId:string,text:string){
    if(!text.trim())throw Error('Введите сообщение.');
    if(this.core?.connectionPhase()!=='online')throw Error('Нет связи с Matrix. Текст сохранён в поле ввода; повторите отправку после подключения.');
    const room=this.core!.listConversations().find(r=>r.id===roomId);
    if(!room?.encrypted)throw Error('Отправка в комнату без E2EE запрещена.');
    if(room.kind==='chat')await this.core!.sendTextMessage(roomId,text,room.recipient);
    else await this.core!.sendGroupMessage(roomId,text);
    await this.activateRoom(roomId);
  }
  async sellerRoom(userId:string,baseUrl:string){if(baseUrl.replace(/\/$/,'')!==this.session?.baseUrl)throw Error('Другой homeserver не подключён.');const id=await this.core!.createDirectConversation(userId);this.refresh();return id}
  async createRoom(name:string,users:string[]){const id=await this.core!.createGroupConversation(name,users);this.refresh();return id}
  async join(roomId:string){await this.core!.joinGroupConversation(roomId);this.refresh()}
  async roomSecurityStatus(roomId:string){const room=this.core!.listConversations().find(r=>r.id===roomId);return {roomEncrypted:!!room?.encrypted,cryptoReady:!!room?.encrypted,ownDeviceSigned:false,ownDeviceVerified:false,unverifiedUsers:[],unsignedMembers:[],sendBlockedReason:room?.encrypted?null:'Комната без сквозного шифрования'}}
  async profileName(name:string){if(this.session){this.session.displayName=name;this.emit({session:this.session});await this.core!.setDisplayName(name)}}
  async deviceSecurity(){return this.core!.deviceSecurity()}
  async ownKey(){return await this.core?.deviceKey()??{ed25519:'',curve25519:''}}
  async devices(){return []}
  async closed(){/* ExistingTransport.stop owns SDK shutdown. */}
  async clearLocalData(){/* Never erase existing message/crypto stores from a UI adapter. */}
  stop(){this.off?.();this.off=undefined;if(this.timer)clearInterval(this.timer);void this.core?.stop();this.core=undefined;this.session=undefined;this.rows.clear();this.drafts.clear();this.emit({phase:'disconnected',rooms:[],session:undefined})}
  async logout(){
    await authRequest('logout',{});
    this.clearActive();
    localStorage.setItem('presidium_auth_changed',String(Date.now()));
  }
  async disconnect(){await this.logout()}
  clearActive(){
    if(this.session)localStorage.removeItem(`presidium_active_user_${encodeURIComponent(this.session.baseUrl)}`);
    setStorageOwner('anonymous');this.stop();
  }
  subscribeVerification(){return()=>{}}
  efficiency(){return {cacheBytes:0,quality:'balanced',readReceiptMode:'private',autoplay:false}}
  async cacheSize(){return 0}
  setEfficiency=unavailable;clearMediaCache=unavailable;exportKeys=unavailable;importKeys=unavailable;deviceRecovery=unavailable;
  editMessage=unavailable;deleteMessage=unavailable;sendFile=unavailable;upload=unavailable;attachment=unavailable;sendAttachment=unavailable;
  readReceipt=unavailable;verify=unavailable;setupCrossSigning=unavailable;verifyDevice=unavailable;
}
export const localMatrix=new LocalMatrix();
let engine:LocalMatrix|undefined;
export function supportedEngine(){return engine??=new Proxy(localMatrix,{get(target,key){const value=Reflect.get(target,key);return typeof value==='function'?value.bind(target):value===undefined?unavailable:value}})}

export async function authRequest(action:string,body?:unknown){
 const response=await fetch('/api/auth/'+action,{method:body===undefined?'GET':'POST',headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
 const value=await response.json();if(!response.ok)throw Object.assign(Error(value.error||'Не удалось выполнить запрос'),{status:response.status});return value;
}
if(typeof window!=='undefined')window.addEventListener('storage',event=>{if(event.key==='presidium_auth_changed'){localMatrix.clearActive();window.location.reload()}});
