/** The LAN proxy preserves Host; only a configured external origin is trusted. */
export function sameOrigin(request:Request){
 const host=request.headers.get('host'),origin=request.headers.get('origin');
 if(!host||!origin)return false;
 const configured=process.env.PRESIDIUM_PUBLIC_ORIGIN;
 if(configured&&host===new URL(configured).host)return origin===configured;
 return origin===`${new URL(request.url).protocol}//${host}`;
}
export function secureRequest(request:Request){const configured=process.env.PRESIDIUM_PUBLIC_ORIGIN;return new URL(request.url).protocol==='https:'||!!(configured?.startsWith('https:')&&request.headers.get('host')===new URL(configured).host)}
