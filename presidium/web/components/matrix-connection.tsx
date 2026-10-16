'use client';
import {lazy,createContext,useContext,useEffect,useRef,useState,useSyncExternalStore,type ReactNode} from 'react';
import {Lock,ChevronRight,WifiOff,ShieldCheck} from 'lucide-react';
import {toast} from 'sonner';
import {filterMatrixRooms} from '@/lib/matrix-inbox';
import type {MatrixTransport} from '@/lib/matrix-transport';
import {MatrixConnection} from '@/lib/matrix-connection';
import {initials,formatTime} from '@/lib/presidium';
import {Dialog,DialogContent} from './ui/dialog';
import {DeferredSurface} from './deferred-surface';
import type {MarketConversationContext} from '@/lib/matrix-marketplace';
const MatrixWorkspace=lazy(()=>import('./matrix-workspace').then(m=>({default:m.MatrixWorkspace})));
const Context=createContext<{connection:MatrixConnection;activeRoom?:string;open:(roomId?:string)=>Promise<boolean>;openSeller:(seller:{userId:string;baseUrl:string},listing:MarketConversationContext['listing'],onOpenListing:()=>void,signal?:AbortSignal)=>Promise<boolean>}|null>(null);
export function MatrixConnectionProvider({children}:{children:ReactNode}){
 const [connection]=useState(()=>new MatrixConnection()),[activeRoom,setActiveRoom]=useState<string>();
 useEffect(()=>{void connection.validate()},[connection]);
 async function open(id?:string){if(!await connection.validate())return false;if(id){const room=connection.snapshot().state.rooms.find(r=>r.id===id);if(room?.membership==='invite'&&room.kind==='group')await connection.snapshot().engine!.join(id);await connection.snapshot().engine!.activateRoom(id)}setActiveRoom(id);return true}
 async function openSeller(seller:{userId:string;baseUrl:string}){if(!await connection.validate())return false;const id=await connection.snapshot().engine!.sellerRoom(seller.userId,seller.baseUrl);setActiveRoom(id);return true}
 return <Context.Provider value={{connection,activeRoom,open,openSeller}}>{children}</Context.Provider>
}
export function useMatrixConnection(){const value=useContext(Context);if(!value)throw Error('Matrix provider is missing');const snapshot=useSyncExternalStore(value.connection.subscribe,value.connection.snapshot,value.connection.snapshot);return {...value,...snapshot}}
export function MatrixInbox({search,tab,onAccountNeeded}:{search:string;tab:string;onAccountNeeded:()=>void}){
 const network=useMatrixConnection(),{state}=network;const rooms=filterMatrixRooms(state.rooms,search,tab);
 async function open(id?:string){try{if(!id||!await network.open(id))onAccountNeeded()}catch(e){toast.error(e instanceof Error?e.message:'Не удалось открыть разговор')}}
 return <><button className="pw-contacts-entry matrix-inbox-entry" onClick={()=>void open()}>{state.phase==='offline'||state.phase==='expired'?<WifiOff size={18}/>:state.verificationPending?<ShieldCheck size={18}/>:<Lock size={18}/>}<span><b>Сетевые чаты</b><small>{state.phase==='expired'?'Восстановить Matrix-вход':state.verificationPending?'Запрос проверки устройства':state.phase==='online'?`${state.rooms.length} комнат · подключено`:state.phase==='offline'?'Нет связи · повторяем подключение':state.phase==='syncing'?'Синхронизация…':'Подключить Matrix'}</small></span>{!!state.outbox?.length&&<span className="badge-count">{state.outbox.length}</span>}<ChevronRight size={16}/></button>{network.error&&<p className="matrix-inbox-note" role="status">{network.error}</p>}{!!rooms.length&&<><div className="section-label">MATRIX<span>{rooms.length}</span></div>{rooms.map(room=><button className="chat-row matrix-network-row" key={room.id} onClick={()=>void open(room.id)}><span className="avatar blue">{initials(room.name)}</span><span className="row-copy"><span className="row-top"><strong>{room.name}</strong><time>{room.lastTime?formatTime(room.lastTime):''}</time></span><span className="row-bottom"><p>{room.membership==='invite'?'Приглашение':room.preview}</p>{room.unread>0&&<span className="badge-count">{room.unread}</span>}</span><small className="matrix-room-source">{room.encrypted?'Matrix · шифровано':'Matrix · отправка запрещена'}{state.outbox?.some(item=>item.roomId===room.id)?' · в очереди':''}</small></span></button>)}</>}</>;
}
