import type {DraftPhoto} from './commissionka-local';
// All listing photos are explicitly public on approval, separate from E2EE attachments.
export async function prepareListingPhoto(file:File):Promise<DraftPhoto>{
 if(file.size>25*1024**2)throw Error('Фото должно быть не больше 25 МБ.');if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('Добавьте JPEG, PNG или WebP.');
 const {imageDimensions}=await import('./photo-codec');const dimensions=await imageDimensions(file);if(!dimensions||dimensions[0]*dimensions[1]>24_000_000)throw Error('Фото должно быть не больше 24 мегапикселей.');
 let bitmap:ImageBitmap|HTMLImageElement;
 if(typeof createImageBitmap!=='undefined')bitmap=await createImageBitmap(file);else bitmap=await new Promise<HTMLImageElement>((resolve,reject)=>{const img=new Image(),url=URL.createObjectURL(file);img.onload=()=>{URL.revokeObjectURL(url);resolve(img)};img.onerror=()=>{URL.revokeObjectURL(url);reject(Error('Не удалось открыть фото.'))};img.src=url});
 const width='naturalWidth' in bitmap?bitmap.naturalWidth:bitmap.width,height='naturalHeight' in bitmap?bitmap.naturalHeight:bitmap.height;
 try{async function encode(max:number,quality:number){const ratio=Math.min(1,max/Math.max(width,height)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(width*ratio));canvas.height=Math.max(1,Math.round(height*ratio));const ctx=canvas.getContext('2d');if(!ctx)throw Error('Обработка фото недоступна.');ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('Не удалось обработать фото.')),'image/webp',quality));if(blob.type!=='image/webp')throw Error('Браузер не поддерживает WebP. Попробуйте другой браузер.');return blob}const blob=await encode(1600,.82),thumbnail=await encode(384,.72);if(blob.size>2*1024**2||thumbnail.size>128*1024)throw Error('Фото слишком сложное для загрузки. Выберите меньшую фотографию.');return {id:crypto.randomUUID(),blob,thumbnail}}
 finally{if('close' in bitmap)bitmap.close()}
}
