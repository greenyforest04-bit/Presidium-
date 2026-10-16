import type {State,Space} from './domain';
export type * from './domain';
export {read,write,removeBlob,saveStoryState} from './storage';
import {read,write,scopeKey} from './storage';
export const seed=(_now=Date.now()):State=>({version:1,spaces:[{id:'saved',name:'Моя капсула',initials:'',color:'orange',kind:'saved',description:'Личные заметки и файлы.'}],messages:[],stories:[],profile:'Вы',compact:false,drafts:{}});
export async function loadState(){return (await read<State>('state','current'))??seed()}
export async function saveState(state:State){await write('state','current',{...state,spaces:state.spaces.filter(s=>!s.id.startsWith('!')),messages:state.messages.filter(m=>!m.spaceId.startsWith('!'))})}
const identityPromises=new Map<string,Promise<{pair:CryptoKeyPair;fingerprint:string}>>();
export function identity(){
 const key=scopeKey('device');
 let identityPromise=identityPromises.get(key);
 if(!identityPromise){identityPromise=(async()=>{
 let pair=await read<CryptoKeyPair>('keys',key);
 if(!pair){pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},false,['sign','verify']);await write('keys',key,pair)}
 const publicKey=await crypto.subtle.exportKey('spki',pair.publicKey);
 const digest=await crypto.subtle.digest('SHA-256',publicKey);
 const fingerprint=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('').toUpperCase().match(/.{1,4}/g)!.join(' ');
 return {pair,fingerprint};
 })().catch(e=>{identityPromises.delete(key);throw e});identityPromises.set(key,identityPromise);}
 return identityPromise;
}
export async function verifyIdentity(){const {pair}=await identity();const challenge=crypto.getRandomValues(new Uint8Array(32));const signature=await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},pair.privateKey,challenge);return crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},pair.publicKey,signature,challenge)}
export function initials(name:string){return name.trim().split(/\s+/).slice(0,2).map(s=>Array.from(s)[0]).join('').toUpperCase()}
export const formatTime=(time:number)=>new Date(time).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});

// Local record IDs, never authentication credentials. Supports HTTP development previews.
export function recordId(){const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const h=Array.from(bytes,b=>b.toString(16).padStart(2,"0")).join("");return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;}
