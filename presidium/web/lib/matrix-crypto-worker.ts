import {Attachment,EncryptedAttachment,OlmMachine,initAsync} from '@matrix-org/matrix-sdk-crypto-wasm';
import {inspectKeys,validateKeyArmor,type CryptoJob} from './matrix-media';
self.onmessage=async(event:MessageEvent<CryptoJob>)=>{const job=event.data;try{await initAsync();let result:unknown;
 if(job.op==='encrypt-file'){const attachment=Attachment.encrypt(new Uint8Array(job.bytes));try{const bytes=attachment.encryptedData;result={bytes:bytes.buffer,info:JSON.parse(attachment.mediaEncryptionInfo!)};self.postMessage({ok:true,result}, {transfer:[bytes.buffer as ArrayBuffer]});return}finally{attachment.free()}}
 if(job.op==='decrypt-file'){const attachment=new EncryptedAttachment(new Uint8Array(job.bytes),JSON.stringify(job.info));try{const bytes=Attachment.decrypt(attachment);result=bytes.buffer;self.postMessage({ok:true,result},{transfer:[bytes.buffer as ArrayBuffer]});return}finally{attachment.free()}}
 if(job.op==='export-keys'){inspectKeys(job.json);result=OlmMachine.encryptExportedRoomKeys(job.json,job.password,500000)}
 if(job.op==='import-keys'){validateKeyArmor(job.armored);result=inspectKeys(OlmMachine.decryptExportedRoomKeys(job.armored,job.password))}
 self.postMessage({ok:true,result});
 }catch{self.postMessage({ok:false,error:job.op==='import-keys'?'Не удалось открыть файл: неверный пароль или повреждённые ключи.':job.op==='decrypt-file'?'Вложение повреждено или ключ не подходит.':'Не удалось завершить шифрование. Повторите операцию.'})}};
