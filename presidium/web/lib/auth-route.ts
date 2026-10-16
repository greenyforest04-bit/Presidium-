import {cookies} from 'next/headers';
import {sameOrigin,secureRequest} from '@/lib/request-origin';
const backend=()=>process.env.PRESIDIUM_API_PROXY_URL||'http://127.0.0.1:8000';
export async function handle(request:Request,action:string){
 if(!['session','login','register','logout','devices','password','lookup'].includes(action))return new Response(null,{status:404});
 if(request.method!=='GET'&&!sameOrigin(request))return Response.json({error:'Недопустимый источник запроса'},{status:403});
 if(request.headers.get('sec-fetch-site')==='cross-site')return new Response(null,{status:403});
 const jar=await cookies(),token=jar.get('presidium_matrix_token')?.value;
 const headers:Record<string,string>={'Content-Type':'application/json'};
 if(token)headers.Authorization='Bearer '+token;
 const body=request.method==='GET'?undefined:await request.text();
 if(body&&body.length>16384)return new Response(null,{status:413});
 let response:Response;
 try{response=await fetch(`${backend()}/v1/auth/${action}${new URL(request.url).search}`,{method:request.method,headers,body,cache:'no-store',redirect:'error'})}
 catch{return Response.json({error:'Сервер Presidium недоступен. Повторите запрос.'},{status:503})}
 const value=await response.json();
 if(response.ok&&['login','register'].includes(action))jar.set('presidium_matrix_token',value.access_token,{httpOnly:true,sameSite:'strict',secure:secureRequest(request),path:'/api',maxAge:2592000});
 if((response.ok&&action==='logout')||(response.status===401&&action==='session'))jar.delete({name:'presidium_matrix_token',path:'/api'});
 return Response.json(response.ok?value:{error:typeof value.detail==='string'?value.detail:'Проверьте заполнение полей.',code:response.status===401?'signin_required':undefined},{status:response.status,headers:{'Cache-Control':'no-store','Pragma':'no-cache'}});
}
