import {cookies} from 'next/headers';
import {handle} from '@/lib/auth-route';
import {secureRequest,sameOrigin} from '@/lib/request-origin';
export async function POST(request:Request){
 if(!sameOrigin(request))return Response.json({error:'Origin rejected'},{status:403});
 const input=await request.json();if(typeof input.accessToken!=='string'||input.accessToken.length>4096)return Response.json({error:'Invalid token'},{status:400});
 const verified=await fetch((process.env.PRESIDIUM_API_PROXY_URL||'http://127.0.0.1:8000')+'/api/account',{headers:{Authorization:'Bearer '+input.accessToken},cache:'no-store'});
 if(!verified.ok)return Response.json({error:'Matrix session rejected'},{status:verified.status});
 (await cookies()).set('presidium_matrix_token',input.accessToken,{httpOnly:true,sameSite:'strict',secure:secureRequest(request),path:'/api',maxAge:30*86400});
 return Response.json({authenticated:true});
}
export async function DELETE(request:Request){return handle(new Request(request,{method:'POST',body:'{}'}),'logout')}
export async function GET(request:Request){return handle(request,'session')}
