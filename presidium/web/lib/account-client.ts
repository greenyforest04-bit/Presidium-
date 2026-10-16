import type {AccountSnapshot,AccountProfile} from './account';
export async function accountRequest(method='GET',body?:unknown){
 let response:Response;try{response=await fetch('/api/account',{method,credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined})}catch{throw Object.assign(Error('Нет связи с сервером. Локальные данные сохранены.'),{code:'network_unavailable'})}
 let result:{error?:string;code?:string}&Partial<AccountSnapshot>&{profile?:AccountProfile|null};try{result=await response.json()}catch{throw Error('Сервер не ответил в ожидаемом формате. Попробуйте снова.')}
 if(!response.ok)throw Object.assign(Error(result.error||'Не удалось выполнить операцию.'),{status:response.status,code:result.code});return result;
}
