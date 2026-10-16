import {MEDIA_POLICY,type MediaMode} from './efficiency-config';
import {MATRIX_FILE_LIMIT,previewMime,validateFileSize} from './matrix-media';

export const VIDEO_MAX_MS=60000;
export type VideoMetadata={duration:number;width:number;height:number};
export type VideoDraft={file:File;metadata:VideoMetadata};
export type VideoState={phase:'idle'|'requesting'|'preview'|'starting'|'recording'|'stopping'|'ready'|'error';elapsed:number;stream?:MediaStream;facing:'user'|'environment';draft?:VideoDraft;error?:string;notice?:string};
export function videoMime(value:string){const type=value.split(';')[0].trim().toLowerCase();return previewMime(type).startsWith('video/')?type:undefined}
export function videoOptions(mode:MediaMode='balanced'):MediaRecorderOptions{
 const mimeType=['video/mp4;codecs=avc1.42E01E,mp4a.40.2','video/webm;codecs=vp8,opus','video/mp4','video/webm'].find(type=>MediaRecorder.isTypeSupported(type));
 return {videoBitsPerSecond:MEDIA_POLICY[mode].videoBitrate,audioBitsPerSecond:32000,...(mimeType?{mimeType}:{})};
}
function dimensions(metadata:VideoMetadata){return Number.isSafeInteger(metadata.width)&&metadata.width>0&&metadata.width<=8192&&Number.isSafeInteger(metadata.height)&&metadata.height>0&&metadata.height<=8192}
export function validateVideo(file:File,metadata:VideoMetadata){
 validateFileSize(file.size);
 if(!videoMime(file.type)||!dimensions(metadata)||!Number.isSafeInteger(metadata.duration)||metadata.duration<250||metadata.duration>VIDEO_MAX_MS)throw Error('Некорректная видео-запись. Запишите сообщение заново.');
 return {duration:metadata.duration,width:metadata.width,height:metadata.height};
}
export function incomingVideo(content:Record<string,unknown>):VideoMetadata|undefined{
 const marker=content['org.presidium.video_message'],info=content.info as {mimetype?:unknown;duration?:unknown;w?:unknown;h?:unknown}|undefined;
 if(content.msgtype!=='m.video'||!marker||typeof marker!=='object'||Array.isArray(marker)||typeof info?.mimetype!=='string'||!videoMime(info.mimetype))return;
 const metadata={duration:info.duration,width:info.w,height:info.h} as VideoMetadata;
 if(!dimensions(metadata)||!Number.isSafeInteger(metadata.duration)||metadata.duration<1||metadata.duration>24*60*60*1000)return;
 return {...metadata};
}
export function videoError(problem:unknown){
 const name=problem instanceof Error?problem.name:'';
 if(name==='NotAllowedError'||name==='SecurityError')return 'Разрешите камеру и микрофон в настройках браузера. Можно также прикрепить готовое видео.';
 if(name==='NotFoundError')return 'Камера или микрофон не найдены. Прикрепите готовое видео.';
 if(name==='NotReadableError')return 'Камера занята. Закройте другое приложение и попробуйте снова.';
 return problem instanceof Error?problem.message:'Не удалось записать видео. Попробуйте снова.';
}
type Capture={generation:number;stream:MediaStream;facing:'user'|'environment';recorder?:MediaRecorder;chunks:Blob[];bytes:number;metadata?:VideoMetadata;started?:number;ended?:number;timer?:ReturnType<typeof setInterval>;deadline?:ReturnType<typeof setTimeout>;watchdog?:ReturnType<typeof setTimeout>;notice?:string};

// Separate camera path: records the native stream, never rotates, crops or upscales it.
export class MatrixVideoRecorder{
 private generation=0;private capture?:Capture;private state:VideoState={phase:'idle',elapsed:0,facing:'user'};
 constructor(private onChange:(state:VideoState)=>void){}
 snapshot(){return this.state}
 private update(value:VideoState){this.state=value;this.onChange(value)}
 private release(capture:Capture){
  clearInterval(capture.timer);clearTimeout(capture.deadline);clearTimeout(capture.watchdog);
  if(capture.recorder){capture.recorder.onstart=null;capture.recorder.onstop=null;capture.recorder.ondataavailable=null;capture.recorder.onerror=null}
  capture.stream.getTracks().forEach(track=>{track.onended=null;track.stop()});
  if(capture.recorder&&capture.recorder.state!=='inactive')try{capture.recorder.stop()}catch{/* The stream has stopped. */}
 }
 cancel(){this.generation++;if(this.capture)this.release(this.capture);this.capture=undefined;this.update({phase:'idle',elapsed:0,facing:'user'})}
 private fail(capture:Capture,problem:unknown){if(capture.generation!==this.generation||this.capture!==capture)return;this.release(capture);this.capture=undefined;this.generation++;this.update({phase:'error',elapsed:0,facing:capture.facing,error:videoError(problem)})}
 async open(facing:'user'|'environment'='user'){
  this.cancel();const generation=this.generation;this.update({phase:'requesting',elapsed:0,facing});let stream:MediaStream|undefined;
  try{
   if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw Error('Запись недоступна в этом окне. Откройте сайт в браузере или прикрепите готовое видео.');
   const video:MediaTrackConstraints&{resizeMode?:ConstrainDOMString}={facingMode:{ideal:facing},resizeMode:{ideal:'none'},width:{ideal:480},height:{ideal:854},aspectRatio:{ideal:9/16},frameRate:{ideal:24,max:30}};
   stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:{ideal:true},noiseSuppression:{ideal:true}},video});
   if(generation!==this.generation){stream.getTracks().forEach(track=>track.stop());return}
   const camera=stream.getVideoTracks().find(track=>track.readyState==='live');
   if(!camera||!stream.getAudioTracks().some(track=>track.readyState==='live'))throw Error('Камера или микрофон не передают данные. Попробуйте снова.');
   const actual=camera.getSettings().facingMode,resolved=actual==='user'||actual==='environment'?actual:facing,capture:Capture={generation,stream,facing:resolved,chunks:[],bytes:0};this.capture=capture;
   stream.getTracks().forEach(track=>{track.onended=()=>{if(this.capture!==capture||capture.generation!==this.generation||this.state.phase==='stopping')return;if(this.state.phase==='recording')this.stop('Камера или микрофон отключены. Проверьте запись.');else this.fail(capture,Error('Камера или микрофон отключены. Откройте камеру снова.'))}});
   this.update({phase:'preview',elapsed:0,stream,facing:resolved});
  }catch(problem){if(generation!==this.generation){stream?.getTracks().forEach(track=>track.stop());return}stream?.getTracks().forEach(track=>track.stop());this.capture=undefined;this.generation++;this.update({phase:'error',elapsed:0,facing,error:videoError(problem)})}
 }
 record(width:number,height:number,mode:MediaMode='balanced'){
  const capture=this.capture;if(!capture||capture.generation!==this.generation||this.state.phase!=='preview')return;
  if(!dimensions({width,height,duration:0})){this.fail(capture,Error('Дождитесь появления изображения с камеры.'));return}
  try{
   if(capture.stream.getTracks().some(track=>track.readyState!=='live'))throw Error('Камера или микрофон отключены.');
   const recorder=new MediaRecorder(capture.stream,videoOptions(mode));capture.recorder=recorder;capture.metadata={width,height,duration:0};
   recorder.ondataavailable=event=>{if(capture.generation!==this.generation||!event.data.size)return;capture.bytes+=event.data.size;if(capture.bytes>MATRIX_FILE_LIMIT){this.fail(capture,Error('Видео больше 25 МБ. Запишите более короткое сообщение.'));return}capture.chunks.push(event.data)};
   recorder.onerror=()=>this.fail(capture,Error('Запись видео прервалась. Запишите сообщение заново.'));
   recorder.onstart=()=>{if(capture.generation!==this.generation)return;clearTimeout(capture.watchdog);capture.started=performance.now();this.update({phase:'recording',elapsed:0,stream:capture.stream,facing:capture.facing});capture.timer=setInterval(()=>{if(capture.generation!==this.generation||this.state.phase!=='recording')return;const elapsed=Math.round(performance.now()-capture.started!);this.update({phase:'recording',elapsed,stream:capture.stream,facing:capture.facing});if(elapsed>=VIDEO_MAX_MS)this.stop('Достигнут предел 1 минуты.')},200);capture.deadline=setTimeout(()=>this.stop('Достигнут предел 1 минуты.'),VIDEO_MAX_MS)};
   recorder.onstop=()=>this.finish(capture);
   this.update({phase:'starting',elapsed:0,stream:capture.stream,facing:capture.facing});capture.watchdog=setTimeout(()=>this.fail(capture,Error('Не удалось начать запись. Попробуйте снова.')),10000);recorder.start(1000);
  }catch(problem){this.fail(capture,problem)}
 }
 stop(notice?:string){
  const capture=this.capture;if(!capture||capture.generation!==this.generation||this.state.phase!=='recording')return;
  capture.ended=performance.now();capture.notice=notice;clearInterval(capture.timer);clearTimeout(capture.deadline);this.update({phase:'stopping',elapsed:Math.round(capture.ended-capture.started!),facing:capture.facing});
  capture.watchdog=setTimeout(()=>this.fail(capture,Error('Видео не удалось завершить. Запишите сообщение заново.')),5000);
  try{if(capture.recorder?.state!=='inactive')capture.recorder?.stop();capture.stream.getTracks().forEach(track=>{track.onended=null;track.stop()})}catch(problem){this.fail(capture,problem)}
 }
 checkFrame(width:number,height:number){const capture=this.capture;if(this.state.phase==='recording'&&capture?.metadata&&(capture.metadata.width!==width||capture.metadata.height!==height))this.stop('Размер кадра изменился. Проверьте запись; новое видео можно снять в другой ориентации.')}
 suspend(){if(this.state.phase==='recording')this.stop('Запись остановлена при уходе со страницы. Проверьте видео.');else if(['requesting','preview','starting'].includes(this.state.phase))this.cancel()}
 private finish(capture:Capture){
  if(capture.generation!==this.generation)return;const duration=Math.round((capture.ended??performance.now())-(capture.started??performance.now())),mime=videoMime(capture.recorder?.mimeType||capture.chunks[0]?.type||'');this.release(capture);this.capture=undefined;
  try{
   if(!mime)throw Error('Браузер записал неподдерживаемый формат. Прикрепите готовое видео.');
   if(duration<250||duration>VIDEO_MAX_MS+1000)throw Error('Длительность записи должна быть от четверти секунды до 1 минуты.');
   const file=new File(capture.chunks,`video-${Date.now()}.${mime.slice(6)}`,{type:mime}),metadata=validateVideo(file,{...capture.metadata!,duration:Math.min(duration,VIDEO_MAX_MS)});
   this.update({phase:'ready',elapsed:metadata.duration,facing:capture.facing,draft:{file,metadata},notice:capture.notice});
  }catch(problem){this.update({phase:'error',elapsed:0,facing:capture.facing,error:videoError(problem)})}
 }
}
