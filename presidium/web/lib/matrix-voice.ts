import {MATRIX_FILE_LIMIT,previewMime,validateFileSize} from './matrix-media';

export const VOICE_MAX_MS=5*60*1000;
export type VoiceMetadata={duration:number;waveform:number[]};
export type VoiceDraft={file:File;metadata:VoiceMetadata};
export type VoiceState={phase:'idle'|'requesting'|'recording'|'stopping'|'ready'|'error';elapsed:number;level?:number;draft?:VoiceDraft;error?:string;notice?:string};
export function audioMime(value:string){const type=value.split(';')[0].trim().toLowerCase();return previewMime(type).startsWith('audio/')?type:undefined}
export function voiceOptions():MediaRecorderOptions{
 const mimeType=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4;codecs=mp4a.40.2','audio/mp4','audio/webm','audio/ogg'].find(type=>MediaRecorder.isTypeSupported(type));
 return {audioBitsPerSecond:mimeType?.includes('opus')?24000:48000,...(mimeType?{mimeType}:{})};
}
export function voiceTime(ms:number){const seconds=Math.max(0,Math.floor((Number.isFinite(ms)?ms:0)/1000));return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`}
export function compressWaveform(samples:number[],bars=64){
 if(!samples.length)return [];const length=Math.min(bars,samples.length);
 return Array.from({length},(_,index)=>{const start=Math.floor(index*samples.length/length),end=Math.floor((index+1)*samples.length/length);return Math.max(0,Math.min(1024,Math.round(Math.max(...samples.slice(start,end)))))});
}
export function validateVoice(file:File,metadata:VoiceMetadata){
 validateFileSize(file.size);
 if(!audioMime(file.type)||!Number.isSafeInteger(metadata.duration)||metadata.duration<250||metadata.duration>VOICE_MAX_MS||!Array.isArray(metadata.waveform)||metadata.waveform.length>120||metadata.waveform.some(value=>!Number.isInteger(value)||value<0||value>1024))throw Error('Некорректная голосовая запись. Запишите сообщение заново.');
 return {duration:metadata.duration,waveform:[...metadata.waveform]};
}
export function incomingVoice(content:Record<string,unknown>):VoiceMetadata|undefined{
 const marker=content['org.matrix.msc3245.voice'],info=content.info as {duration?:unknown;mimetype?:unknown}|undefined,details=content['org.matrix.msc1767.audio'] as {duration?:unknown;waveform?:unknown}|undefined;
 if(content.msgtype!=='m.audio'||!marker||typeof marker!=='object'||Array.isArray(marker)||typeof info?.mimetype!=='string'||!audioMime(info.mimetype))return;
 const duration=details?.duration??info.duration,waveform=details?.waveform??[];
 if(typeof duration!=='number'||!Number.isSafeInteger(duration)||duration<1||duration>24*60*60*1000||!Array.isArray(waveform)||waveform.length>120||waveform.some(value=>!Number.isInteger(value)||value<0||value>1024))return;
 return {duration,waveform:[...waveform]};
}
export function voiceError(problem:unknown){
 const name=problem instanceof Error?problem.name:'';
 if(name==='NotAllowedError'||name==='SecurityError')return 'Разрешите доступ к микрофону в настройках браузера и попробуйте снова.';
 if(name==='NotFoundError')return 'Микрофон не найден. Можно прикрепить готовый аудиофайл.';
 if(name==='NotReadableError')return 'Микрофон занят. Закройте другое приложение и попробуйте снова.';
 return problem instanceof Error?problem.message:'Не удалось записать звук. Попробуйте снова.';
}
type Capture={generation:number;stream:MediaStream;recorder:MediaRecorder;chunks:Blob[];bytes:number;started?:number;ended?:number;timer?:ReturnType<typeof setInterval>;deadline?:ReturnType<typeof setTimeout>;watchdog?:ReturnType<typeof setTimeout>;context?:AudioContext;analyser?:AnalyserNode;samples:number[];notice?:string};

// One owner for the permission request, recorder, stream and analysis context.
// Cancel cannot dismiss an OS permission prompt: late streams are stopped.
export class MatrixVoiceRecorder{
 private generation=0;private capture?:Capture;private state:VoiceState={phase:'idle',elapsed:0};
 constructor(private onChange:(state:VoiceState)=>void){}
 snapshot(){const started=this.capture?.started;return this.state.phase==='recording'&&started!==undefined?{...this.state,elapsed:Math.round(performance.now()-started)}:this.state}
 private update(value:VoiceState){this.state=value;this.onChange(value)}
 private release(capture:Capture){
  clearInterval(capture.timer);clearTimeout(capture.deadline);clearTimeout(capture.watchdog);
  capture.recorder.onstart=null;capture.recorder.onstop=null;capture.recorder.ondataavailable=null;capture.recorder.onerror=null;
  capture.stream.getTracks().forEach(track=>{track.onended=null;track.stop()});
  if(capture.recorder.state!=='inactive')try{capture.recorder.stop()}catch{/* Tracks are already stopped. */}
  if(capture.context)void capture.context.close().catch(()=>{});
 }
 cancel(){this.generation++;if(this.capture)this.release(this.capture);this.capture=undefined;this.update({phase:'idle',elapsed:0})}
 private fail(capture:Capture,problem:unknown){if(capture.generation!==this.generation||this.capture!==capture)return;this.release(capture);this.capture=undefined;this.generation++;this.update({phase:'error',elapsed:0,error:voiceError(problem)})}
 async start(){
  this.cancel();const generation=this.generation;this.update({phase:'requesting',elapsed:0});
  let stream:MediaStream|undefined;
  try{
   if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw Error('Запись недоступна в этом окне. Откройте сайт в браузере или прикрепите аудиофайл.');
   stream=await navigator.mediaDevices.getUserMedia({video:false,audio:{channelCount:{ideal:1},echoCancellation:{ideal:true},noiseSuppression:{ideal:true}}});
   if(generation!==this.generation){stream.getTracks().forEach(track=>track.stop());return}
   if(!stream.getAudioTracks().some(track=>track.readyState==='live'))throw Error('Микрофон не передаёт звук. Попробуйте снова.');
   const recorder=new MediaRecorder(stream,voiceOptions()),capture:Capture={generation,stream,recorder,chunks:[],bytes:0,samples:[]};this.capture=capture;
   recorder.ondataavailable=event=>{if(generation!==this.generation||this.capture!==capture||!event.data.size)return;capture.bytes+=event.data.size;if(capture.bytes>MATRIX_FILE_LIMIT){this.fail(capture,Error('Запись больше 25 МБ. Запишите более короткое сообщение.'));return}capture.chunks.push(event.data)};
   recorder.onerror=()=>this.fail(capture,Error('Запись прервалась из-за ошибки микрофона. Запишите сообщение снова.'));
   recorder.onstart=()=>{
    if(generation!==this.generation||this.capture!==capture)return;clearTimeout(capture.watchdog);capture.started=performance.now();
    this.update({phase:'recording',elapsed:0});
    capture.timer=setInterval(()=>this.tick(capture),200);capture.deadline=setTimeout(()=>this.stop('Достигнут предел 5 минут.'),VOICE_MAX_MS);
   };
   recorder.onstop=()=>this.finish(capture);
   stream.getAudioTracks().forEach(track=>{track.onended=()=>{if(this.capture===capture&&capture.generation===this.generation)this.stop('Микрофон отключён. Проверьте запись перед отправкой.')}});
   // A meter is optional. Failure never fabricates samples or blocks recording.
   try{const Context=window.AudioContext||(window as typeof window&{webkitAudioContext?:typeof AudioContext}).webkitAudioContext;if(Context){capture.context=new Context();capture.analyser=capture.context.createAnalyser();capture.analyser.fftSize=256;capture.context.createMediaStreamSource(stream).connect(capture.analyser);void capture.context.resume().catch(()=>{})}}catch{/* Keep recording without a waveform. */}
   capture.watchdog=setTimeout(()=>this.fail(capture,Error('Микрофон не начал запись. Попробуйте снова.')),10000);
   recorder.start(1000);
  }catch(problem){if(generation!==this.generation){stream?.getTracks().forEach(track=>track.stop());return}if(this.capture)this.release(this.capture);else stream?.getTracks().forEach(track=>track.stop());this.capture=undefined;this.generation++;this.update({phase:'error',elapsed:0,error:voiceError(problem)})}
 }
 private tick(capture:Capture){
  if(capture.generation!==this.generation||this.capture!==capture||capture.started===undefined||this.state.phase!=='recording')return;
  let level:number|undefined;
  if(capture.analyser&&capture.context?.state==='running')try{const data=new Uint8Array(capture.analyser.fftSize);capture.analyser.getByteTimeDomainData(data);let energy=0;for(const value of data)energy+=((value-128)/128)**2;level=Math.min(1024,Math.round(Math.sqrt(energy/data.length)*4096));capture.samples.push(level)}catch{/* Optional analysis may be suspended by the browser. */}
  const elapsed=Math.round(performance.now()-capture.started);this.update({phase:'recording',elapsed,level});if(elapsed>=VOICE_MAX_MS)this.stop('Достигнут предел 5 минут.');
 }
 stop(notice?:string){
  const capture=this.capture;if(!capture||capture.generation!==this.generation||this.state.phase!=='recording')return;
  capture.ended=performance.now();capture.notice=notice;clearInterval(capture.timer);clearTimeout(capture.deadline);
  this.update({phase:'stopping',elapsed:Math.round(capture.ended-capture.started!)});
  capture.watchdog=setTimeout(()=>this.fail(capture,Error('Запись не удалось завершить. Запишите сообщение заново.')),5000);
  try{if(capture.recorder.state!=='inactive')capture.recorder.stop();capture.stream.getTracks().forEach(track=>track.stop())}catch(problem){this.fail(capture,problem)}
 }
 private finish(capture:Capture){
  if(capture.generation!==this.generation||this.capture!==capture)return;
  const duration=Math.round((capture.ended??performance.now())-(capture.started??performance.now())),mime=audioMime(capture.recorder.mimeType||capture.chunks[0]?.type||'');
  this.release(capture);this.capture=undefined;
  try{
   if(!mime)throw Error('Браузер записал неподдерживаемый формат звука. Прикрепите готовый аудиофайл.');
   if(duration<250)throw Error('Запись слишком короткая. Удерживайте микрофон и говорите или нажмите для записи без удержания.');
   if(duration>VOICE_MAX_MS+1000)throw Error('Запись длиннее 5 минут. Запишите более короткое сообщение.');
   const extension=mime==='audio/mp4'?'m4a':mime.slice(6),blob=new Blob(capture.chunks,{type:mime}),file=new File([blob],`voice-${Date.now()}.${extension}`,{type:mime}),metadata=validateVoice(file,{duration:Math.min(duration,VOICE_MAX_MS),waveform:compressWaveform(capture.samples)});
   this.update({phase:'ready',elapsed:metadata.duration,draft:{file,metadata},notice:capture.notice});
  }catch(problem){this.update({phase:'error',elapsed:0,error:voiceError(problem)})}
 }
}
