/** Fixed-target Matrix proxy. Preserve protocol paths, including pushrules/. */
async function proxy(request:Request){
 const source=new URL(request.url),target=process.env.PRESIDIUM_MATRIX_PROXY_URL||'http://127.0.0.1:8008';
 // Credential mutations go through the validated, rate-limited auth boundary.
 if(request.method!=='GET'&&/^\/_matrix\/client\/[^/]+\/(login|register|logout(?:\/all)?|account\/password|delete_devices|devices\/[^/]+)\/?$/.test(decodeURIComponent(source.pathname)))return Response.json({errcode:'M_FORBIDDEN',error:'Use Presidium account settings'},{status:403});
 const headers=new Headers();
 for(const name of ['authorization','content-type','accept']){const value=request.headers.get(name);if(value)headers.set(name,value)}
 const body=['GET','HEAD'].includes(request.method)?undefined:await request.arrayBuffer();
 if(body&&body.byteLength>26_000_000)return new Response(null,{status:413});
 try{
  if(headers.has('authorization')){
   const policy=await fetch((process.env.PRESIDIUM_API_PROXY_URL||'http://127.0.0.1:8000')+'/v1/auth/policy',{headers:{Authorization:headers.get('authorization')!},cache:'no-store'});
   if(!policy.ok)return Response.json({errcode:policy.status===401?'M_UNKNOWN_TOKEN':'M_UNAVAILABLE',error:'Session policy rejected request'},{status:policy.status});
  }
  const response=await fetch(target+source.pathname+source.search,{method:request.method,headers,body,cache:'no-store',redirect:'manual'});
  const result=new Headers({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
  for(const name of ['content-type','retry-after','content-disposition']){const value=response.headers.get(name);if(value)result.set(name,value)}
  return new Response(response.body,{status:response.status,headers:result});
 }catch{return Response.json({errcode:'M_UNAVAILABLE',error:'Matrix server unavailable'},{status:502})}
}
export {proxy as GET,proxy as POST,proxy as PUT,proxy as DELETE,proxy as PATCH,proxy as OPTIONS,proxy as HEAD};
