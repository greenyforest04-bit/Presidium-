import {cookies} from 'next/headers';
import {sameOrigin} from '@/lib/request-origin';
import {productPath} from '@/lib/product-route';
async function proxy(request:Request,{params}:{params:Promise<{path:string[]}>}){
 if(!['GET','HEAD'].includes(request.method)&&!sameOrigin(request))return Response.json({error:'Недопустимый источник запроса'},{status:403});
 const {path}=await params;const url=new URL(request.url),jar=await cookies();
 const token=jar.get('presidium_matrix_token')?.value;
 const headers=new Headers();if(token)headers.set('Authorization','Bearer '+token);
 if(request.headers.get('content-type'))headers.set('Content-Type',request.headers.get('content-type')!);
 const accountWrite=path[0]==='account'&&['PATCH','DELETE'].includes(request.method);
 const method=accountWrite?'POST':request.method;
 let body=['GET','HEAD'].includes(method)?undefined:await request.arrayBuffer();
 if(path[0]==='account'&&request.method==='DELETE'){const input=JSON.parse(new TextDecoder().decode(body));body=new TextEncoder().encode(JSON.stringify({action:'revoke',sessionId:input.sessionId})).buffer;}
 const response=await fetch((process.env.PRESIDIUM_API_PROXY_URL||'http://127.0.0.1:8000')+'/api/'+productPath(path).map(encodeURIComponent).join('/')+url.search,{method,headers,body,cache:'no-store',redirect:'error'});
 if(!response.headers.get('content-type')?.includes('application/json'))return new Response(await response.arrayBuffer(),{status:response.status,headers:{'content-type':response.headers.get('content-type')||'application/octet-stream','cache-control':'no-store','x-content-type-options':'nosniff'}});
 let value=await response.json();
 if(!response.ok){const problem=value.detail;value={...value,error:typeof problem==='string'?problem:problem?.error||value.error||'Операция недоступна',code:problem?.code||value.code||(response.status===401?'signin_required':undefined)};}
 if(response.status===401)value.error='Подключите Matrix в разделе «Сообщения», чтобы открыть серверные функции.';
 else if(path.join('/')==='account'&&method==='GET')value={...value,authenticated:true,sessionActive:true,identity:{displayName:value.profile.name,email:value.profile.userId}};
 return Response.json(value,{status:response.status,headers:{'cache-control':'no-store'}});
}
export {proxy as GET,proxy as POST,proxy as PATCH,proxy as DELETE};
