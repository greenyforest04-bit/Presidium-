import {handle} from '@/lib/auth-route';
async function route(request:Request,{params}:{params:Promise<{action:string}>}){return handle(request,(await params).action)}
export {route as GET,route as POST};
