import {z} from 'zod';
export const profileInput=z.object({name:z.string().trim().min(1,'Введите имя.').max(80,'Имя должно быть короче 80 символов.'),handle:z.string().trim().transform(v=>v.replace(/^@/,'').toLowerCase()).refine(v=>!v||/^[a-z0-9._]{3,32}$/.test(v),'Имя пользователя: 3–32 символа, латиница, цифры, точка или подчёркивание.'),bio:z.string().trim().max(500,'Описание: максимум 500 символов.')});
export const connectInput=z.object({profile:profileInput,deviceName:z.string().trim().min(1).max(80)});
export const updateInput=profileInput.and(z.object({revision:z.number().int().positive()}));
export const revokeInput=z.object({sessionId:z.string().min(1).max(100)});
export type ProfileInput=z.infer<typeof profileInput>;
export type AccountProfile=ProfileInput&{userId:string;revision:number;updatedAt:number};
export type AccountDevice={id:string;label:string;createdAt:number;lastSeenAt:number;expiresAt:number;current:boolean};
export type AccountSnapshot={authenticated:boolean;identity?:{displayName:string;email:string};profile:AccountProfile|null;sessionActive:boolean;devices:AccountDevice[]};
export class AccountError extends Error{constructor(message:string,public status:number,public code?:string){super(message)}}
export function originWriteGuard(request:Request){
 if(request.headers.get('origin')!==new URL(request.url).origin)throw new AccountError('Запрос с другого сайта отклонён.',403,'invalid_origin');
}
export function accountWriteGuard(request:Request){
 originWriteGuard(request);
 if(!request.headers.get('content-type')?.toLowerCase().startsWith('application/json'))throw new AccountError('Нужен запрос JSON.',415,'invalid_content_type');
}
export async function readAccountJson(request:Request):Promise<unknown>{
 const reader=request.body?.getReader();if(!reader)throw new AccountError('Пустой запрос.',400);const parts:Uint8Array[]=[];let size=0;
 try{while(true){const result=await reader.read();if(result.done)break;size+=result.value.length;if(size>8192){await reader.cancel();throw new AccountError('Запрос слишком большой.',413)}parts.push(result.value)}}finally{reader.releaseLock()}
 const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length}
 try{return JSON.parse(new TextDecoder().decode(bytes))}catch{throw new AccountError('Не удалось прочитать JSON.',400)}
}
