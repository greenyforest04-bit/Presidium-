import {MatrixTransport,type TransportState} from './matrix-transport';
import {localMatrix,supportedEngine} from './local-matrix';
export type ConnectionSnapshot={owner?:string;engine?:MatrixTransport;state:TransportState;error?:string;offlineAccess?:boolean};
export class MatrixConnection {
 private value:ConnectionSnapshot={state:localMatrix.snapshot()};private listeners=new Set<()=>void>();private resuming?:Promise<void>;
 constructor(){localMatrix.subscribe(state=>{this.value={state,owner:state.session?.userId,engine:state.session?supportedEngine() as unknown as MatrixTransport:undefined,error:state.error};for(const listener of this.listeners)listener()})}
 snapshot=()=>this.value;
 subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener)}};
 async validate(){if(typeof window==='undefined')return false;if(!localMatrix.session){this.resuming??=localMatrix.resume().finally(()=>{this.resuming=undefined});try{await this.resuming}catch{}}return !!localMatrix.session}
 stop(){/* Application-owned singleton; modal unmount must not stop transport or erase keys. */}
}
