import type {Story} from './presidium';

export const STORY_MAX_BYTES=25*1024*1024;
export const STORY_MAX_SECONDS=60;
export type CameraSide='user'|'environment';
export type CaptureMode='photo'|'video';

export function cameraConstraints(side:CameraSide,mode:CaptureMode):MediaStreamConstraints {
 // Request the sensor's native format; iOS rotates it with the phone. Forcing
 // portrait width/height produces a landscape crop in WebKit (bug 323550).
 return {audio:mode==='video',video:{facingMode:{ideal:side},width:{ideal:1280},height:{ideal:720},resizeMode:{ideal:'none'}} as MediaTrackConstraints};
}

export function bindStoryCamera(video:HTMLVideoElement,stream:MediaStream,onReady:()=>void,onError:()=>void){
 let active=true,ready=false,frame:number|undefined;
 function firstFrame(){
  if(!active||ready||video.srcObject!==stream||video.readyState<2||!video.videoWidth||!video.videoHeight)return;
  ready=true;onReady();
 }
 // Track settings may still contain the unrotated dimensions before this frame.
 const frameCallbacks=typeof video.requestVideoFrameCallback==='function';
 const loaded=()=>{if(!frameCallbacks)firstFrame();};
 video.addEventListener('loadeddata',loaded);
 video.srcObject=stream;
 if(frameCallbacks)frame=video.requestVideoFrameCallback(firstFrame);
 void video.play().then(loaded).catch(()=>{if(active)onError();});
 return()=>{active=false;video.removeEventListener('loadeddata',loaded);if(frame!==undefined)video.cancelVideoFrameCallback(frame);if(video.srcObject===stream)video.srcObject=null;};
}
export function cameraError(error:unknown){
 const name=error instanceof Error?error.name:'';
 if(name==='NotAllowedError'||name==='SecurityError')return 'Разрешите доступ к камере в настройках браузера. Фото и видео можно выбрать из галереи.';
 if(name==='NotFoundError')return 'Камера не найдена. Выберите фото или видео из галереи.';
 if(name==='NotReadableError')return 'Камера занята другим приложением. Закройте его и попробуйте ещё раз.';
 return error instanceof Error?error.message:'Не удалось открыть камеру. Попробуйте ещё раз или откройте галерею.';
}
export function boundedMusic(start:number,length:number,duration:number){
 const total=Number.isFinite(duration)?Math.max(0,duration):0;
 const span=Math.min(total,30,Math.max(.1,length));
 return {start:Math.max(0,Math.min(start,total-span)),length:span};
}
export function storyBlobIds(story:Story){return [...new Set([story.blobId,...(story.media||[]).map(a=>a.id),story.music?.id].filter((id):id is string=>!!id))];}
export function storyProblem(story:Story){
 if(!story.media?.length&&!story.blobId&&!story.text.trim()&&!story.layers?.some(l=>l.text.trim())&&!story.poll)return 'Добавьте фото, видео, текст или опрос.';
 if(story.poll&&(!story.poll.question.trim()||story.poll.options.length<2||story.poll.options.some(o=>!o.trim())))return 'Заполните вопрос и варианты ответа.';
 if(story.music&&(!story.music.duration||story.music.length<=0||story.music.start<0||story.music.start+story.music.length>story.music.duration+.05))return 'Дождитесь загрузки музыки и выберите фрагмент.';
 return '';
}
export function recordingOptions():MediaRecorderOptions {
 const mimeType=['video/mp4;codecs=avc1.42E01E,mp4a.40.2','video/webm;codecs=vp8,opus','video/mp4','video/webm'].find(type=>MediaRecorder.isTypeSupported(type));
 return {...(mimeType?{mimeType}:{}),videoBitsPerSecond:2_500_000,audioBitsPerSecond:128_000};
}
export async function inspectMedia(blob:Blob,kind:'image'|'video'|'audio'){
 const url=URL.createObjectURL(blob);
 try {
  return await new Promise<{width:number;height:number;duration:number}>((resolve,reject)=>{
   const el=kind==='image'?new Image():document.createElement(kind);
   const timeout=setTimeout(()=>finish(Error('Файл не удалось прочитать. Выберите другой.')),15000);
   function finish(error?:Error){clearTimeout(timeout);el.onerror=null;if(el instanceof HTMLImageElement)el.onload=null;else{el.onloadedmetadata=null;el.removeAttribute('src');el.load();}if(error)reject(error);}
   el.onerror=()=>finish(Error('Этот формат не воспроизводится в браузере. Выберите другой файл.'));
   if(el instanceof HTMLImageElement){el.onload=()=>{const result={width:el.naturalWidth,height:el.naturalHeight,duration:0};finish();resolve(result);};}
   else {el.preload='metadata';el.onloadedmetadata=()=>{const duration=el.duration;if(!Number.isFinite(duration)||duration<=0){finish(Error('Не удалось определить длительность файла.'));return;}const result={width:el instanceof HTMLVideoElement?el.videoWidth:0,height:el instanceof HTMLVideoElement?el.videoHeight:0,duration};finish();resolve(result);};}
   el.src=url;
  });
 } finally {URL.revokeObjectURL(url);}
}

export async function prepareStoryAssets(story:Story,readBlob:(id:string)=>Promise<Blob|undefined>){
 const blobs=new Map(await Promise.all(storyBlobIds(story).map(async id=>{
  const blob=await readBlob(id);
  if(!blob?.size)throw Error('Медиафайл недоступен. Выберите его заново перед публикацией.');
  return [id,blob] as const;
 })));
 const media=await Promise.all((story.media||[]).map(async asset=>{
  if(asset.kind!=='video')return asset;
  const duration=asset.duration||(await inspectMedia(blobs.get(asset.id)!,'video')).duration;
  if(duration>STORY_MAX_SECONDS+.2)throw Error('Видео длиннее 60 секунд. Выберите короткий фрагмент.');
  return {...asset,duration};
 }));
 return {...story,media,duration:media.find(a=>a.kind==='video')?.duration||story.music?.length||8};
}

// Owns the stream across async permission dialogs and rapid mode switches.
export class StoryCameraSession {
 private generation=0;
 stream:MediaStream|null=null;
 stop(){this.generation++;this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;}
 async open(side:CameraSide,mode:CaptureMode){
  this.stop();const generation=this.generation;
  if(!navigator.mediaDevices?.getUserMedia)throw Error('Камера недоступна в этом окне. Откройте сайт в браузере или выберите файл.');
  const constraints=cameraConstraints(side,mode);
  const stream=await navigator.mediaDevices.getUserMedia(constraints);
  if(generation!==this.generation){stream.getTracks().forEach(t=>t.stop());return null;}
  this.stream=stream;
  const track=stream.getVideoTracks()[0];
  try {const caps=track.getCapabilities?.() as MediaTrackCapabilities&{zoom?:{min:number;max:number}};
   if(caps?.zoom)await track.applyConstraints({...constraints.video as MediaTrackConstraints,advanced:[{zoom:Math.max(caps.zoom.min,Math.min(1,caps.zoom.max))} as MediaTrackConstraintSet]});
  }catch{/* Zoom is optional; display always preserves the complete source frame. */}
  if(generation!==this.generation){stream.getTracks().forEach(t=>t.stop());return null;}
  return stream;
 }
}
