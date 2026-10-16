import {z} from 'zod';
export const securityAction=z.discriminatedUnion('action',[
 z.object({action:z.literal('challenge')}),
 z.object({action:z.literal('register'),challenge:z.string().regex(/^[a-f0-9]{64}$/),publicKey:z.string().min(80).max(200),signature:z.string().min(80).max(100)}),
]);
export type SecurityDevice={id:string;label:string;current:boolean;publicKey:string|null;fingerprint:string|null;registeredAt:number|null};
export type SecuritySnapshot={userId:string;sessionId:string;devices:SecurityDevice[]};
export function proofText(userId:string,sessionId:string,challenge:string,publicKey:string){return JSON.stringify(['presidium-device-key-v1',userId,sessionId,challenge,publicKey])}
export function encodeBytes(bytes:Uint8Array){return btoa(Array.from(bytes,v=>String.fromCharCode(v)).join(''))}
export function decodeBytes(value:string){const binary=atob(value);return Uint8Array.from(binary,v=>v.charCodeAt(0))}
export async function keyFingerprint(spki:Uint8Array){const hash=await crypto.subtle.digest('SHA-256',spki as BufferSource);return Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,'0')).join('')}
export function formatFingerprint(value:string){return value.toUpperCase().match(/.{1,4}/g)?.join(' ')||value}
