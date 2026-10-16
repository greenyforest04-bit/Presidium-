import {database,read} from './storage';
import {encodeBytes,keyFingerprint} from './device-security';
const keyId=(user:string,session:string)=>'server-device-key-v1:'+user+':'+session;
export async function localDeviceKey(user:string,session:string,create=false){
 const id=keyId(user,session),existing=await read<CryptoKeyPair>('keys',id);if(existing)return existing;if(!create)return undefined;
 const generated=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},false,['sign','verify']);
 const db=await database();return new Promise<CryptoKeyPair>((resolve,reject)=>{const tx=db.transaction('keys','readwrite'),store=tx.objectStore('keys'),get=store.get(id);let chosen=generated;get.onsuccess=()=>{if(get.result)chosen=get.result;else store.add(generated,id)};tx.oncomplete=()=>resolve(chosen);tx.onabort=()=>reject(tx.error||Error('Не удалось сохранить ключ устройства.'));tx.onerror=()=>reject(tx.error)});
}
export async function publicDeviceKey(pair:CryptoKeyPair){const bytes=new Uint8Array(await crypto.subtle.exportKey('spki',pair.publicKey));return {publicKey:encodeBytes(bytes),fingerprint:await keyFingerprint(bytes)}}
export async function trustedDevices(user:string){return await read<Record<string,string>>('keys','device-trust-v1:'+user)||{}}
export async function trustDevice(user:string,session:string,fingerprint:string|null){
 const db=await database();return new Promise<void>((resolve,reject)=>{const tx=db.transaction('keys','readwrite'),store=tx.objectStore('keys'),id='device-trust-v1:'+user,get=store.get(id);get.onsuccess=()=>{const pins={...(get.result||{})};if(fingerprint)pins[session]=fingerprint;else delete pins[session];store.put(pins,id)};tx.oncomplete=()=>resolve();tx.onabort=()=>reject(tx.error||Error('Не удалось сохранить подтверждение.'));tx.onerror=()=>reject(tx.error)});
}
