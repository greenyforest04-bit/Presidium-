import {z} from 'zod';
export const agentInput=z.object({requestId:z.string().uuid().optional(),question:z.string().trim().min(1).max(800),materials:z.array(z.object({id:z.string().min(1).max(200),title:z.string().max(120),text:z.string().max(600)})).max(4)});
export type AgentInput=z.infer<typeof agentInput>;
export type AgentConfig={url?:string;token?:string};
export function agentConfigured(config:AgentConfig){try{const url=new URL(config.url||'');return url.protocol==='https:'&&!url.username&&!url.password}catch{return false}}
export async function askCapsuleAgent(input:AgentInput,config:AgentConfig,signal:AbortSignal,send:typeof fetch=fetch){
 if(!agentConfigured(config))throw Error('ИИ-сервис не подключён.');
 const response=await send(config.url!,{method:'POST',redirect:'error',signal,headers:{'Content-Type':'application/json',...(config.token?{Authorization:'Bearer '+config.token}:{})},body:JSON.stringify({question:input.question,materials:input.materials,max_output_tokens:1600})});
 if(!response.ok)throw Error(response.status===429?'ИИ-сервис занят. Повторите запрос позже.':'ИИ-сервис не смог выполнить запрос.');
 const reader=response.body?.getReader();if(!reader)throw Error('ИИ-сервис вернул пустой ответ.');let size=0;const parts:Uint8Array[]=[];
 try{while(true){const result=await reader.read();if(result.done)break;size+=result.value.byteLength;if(size>65536){await reader.cancel();throw Error('Ответ ИИ-сервиса слишком большой.')}parts.push(result.value)}}finally{reader.releaseLock()}
 const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length}
 let value:unknown;try{value=JSON.parse(new TextDecoder().decode(bytes))}catch{throw Error('Не удалось прочитать ответ ИИ-сервиса.')}
 const answer=(value as {answer?:unknown}|null)?.answer;
 if(typeof answer!=='string'||!answer.trim()||answer.length>10000)throw Error('ИИ-сервис вернул некорректный ответ.');
 return answer;
}
