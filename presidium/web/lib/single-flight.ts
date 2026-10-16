export function abortable<T>(task:Promise<T>,signal?:AbortSignal):Promise<T>{
 if(!signal)return task;if(signal.aborted)return Promise.reject(new DOMException('Операция отменена','AbortError'));
 return new Promise((resolve,reject)=>{const abort=()=>{cleanup();reject(new DOMException('Операция отменена','AbortError'))},cleanup=()=>signal.removeEventListener('abort',abort);signal.addEventListener('abort',abort,{once:true});task.then(v=>{cleanup();resolve(v)},e=>{cleanup();reject(e)})});
}
export class SingleFlight{
 private work=new Map<string,Promise<unknown>>();
 run<T>(key:string,operation:()=>Promise<T>,signal?:AbortSignal):Promise<T>{if(signal?.aborted)return Promise.reject(new DOMException('Операция отменена','AbortError'));let task=this.work.get(key) as Promise<T>|undefined;if(!task){task=Promise.resolve().then(operation);this.work.set(key,task);void task.finally(()=>{if(this.work.get(key)===task)this.work.delete(key)}).catch(()=>{})}return abortable(task,signal)}
}
