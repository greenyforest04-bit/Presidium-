import type {AgentState} from './presidium-agent';

type VoiceCallbacks={onLevel?:(rms:number)=>void;onState?:(state:AgentState)=>void;onError?:(message:string)=>void;onComplete?:(recording:VoiceRecording)=>void};
export type VoiceRecording={blob:Blob;duration:number};
export type VoiceRecorder={start:()=>Promise<void>;stop:()=>Promise<VoiceRecording>;cancel:()=>void;active:()=>boolean};
const microphoneError=(error:unknown)=>{const name=(error as {name?:string})?.name;if(name==='NotAllowedError')return 'Доступ к микрофону запрещён. Разрешите его в настройках браузера.';if(name==='NotFoundError')return 'Микрофон не найден.';if(name==='NotReadableError')return 'Микрофон занят другим приложением.';return error instanceof Error?error.message:'Не удалось открыть микрофон.'};
export function recordingSupported(){return typeof window!=='undefined'&&!!navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder!=='undefined'}
/** Native browser recorder; the level is computed from actual microphone samples, never generated. */
export function createVoiceRecorder(callbacks:VoiceCallbacks={}):VoiceRecorder{
 let stream:MediaStream|undefined,recorder:MediaRecorder|undefined,context:AudioContext|undefined,analyser:AnalyserNode|undefined,source:MediaStreamAudioSourceNode|undefined;
 let chunks:Blob[]=[],frame=0,timer:ReturnType<typeof setTimeout>|undefined,started=0,generation=0,recording=false,starting=false,size=0;
 let pending:{resolve:(value:VoiceRecording)=>void;reject:(error:unknown)=>void}|undefined,completed:VoiceRecording|undefined,problem:Error|undefined;
 const cleanup=()=>{if(frame)cancelAnimationFrame(frame);frame=0;if(timer)clearTimeout(timer);timer=undefined;source?.disconnect();analyser?.disconnect();source=undefined;analyser=undefined;stream?.getTracks().forEach(t=>t.stop());stream=undefined;if(context){void context.close().catch(()=>{});context=undefined}callbacks.onLevel?.(0);recording=false};
 const cancel=()=>{generation++;starting=false;const previous=recorder;recorder=undefined;if(previous){previous.ondataavailable=null;previous.onstop=null;previous.onerror=null;if(previous.state!=='inactive')try{previous.stop()}catch{}}cleanup();chunks=[];completed=undefined;pending?.reject(new DOMException('Запись отменена','AbortError'));pending=undefined;callbacks.onState?.('CANCELLED')};
 async function start(){
  if(recording||starting)return;if(!recordingSupported())throw new Error('Запись звука недоступна в этом браузере.');
  const token=++generation;starting=true;problem=undefined;completed=undefined;chunks=[];size=0;
  try{
   const media=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
   if(token!==generation){media.getTracks().forEach(t=>t.stop());return}stream=media;
   const mime=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(m=>MediaRecorder.isTypeSupported(m));
   recorder=new MediaRecorder(media,mime?{mimeType:mime}:undefined);const local=recorder;
   local.ondataavailable=event=>{if(token!==generation)return;if(event.data.size){chunks.push(event.data);size+=event.data.size;if(size>8*1024*1024){problem=new Error('Запись превышает 8 МБ.');if(local.state!=='inactive')local.stop()}}};
   local.onerror=()=>{problem=new Error('Браузер прервал запись.');cleanup();pending?.reject(problem);pending=undefined;callbacks.onState?.('ERROR');callbacks.onError?.(problem.message)};
   local.onstop=()=>{if(token!==generation)return;const duration=Math.max(0,(performance.now()-started)/1000),blob=new Blob(chunks,{type:local.mimeType||mime||'audio/webm'});chunks=[];cleanup();recorder=undefined;if(problem||!blob.size){problem??=new Error('Запись пустая. Попробуйте ещё раз.');pending?.reject(problem);callbacks.onState?.('ERROR');callbacks.onError?.(problem.message)}else{completed={blob,duration};pending?.resolve(completed);callbacks.onState?.('IDLE');callbacks.onComplete?.(completed)}pending=undefined};
   // A recorder still works without an AudioContext. Do not synthesize a waveform in that case.
   const AudioCtor=window.AudioContext||(window as unknown as {webkitAudioContext?:typeof AudioContext}).webkitAudioContext;
   if(AudioCtor)try{context=new AudioCtor();void context.resume().catch(()=>{});source=context.createMediaStreamSource(media);analyser=context.createAnalyser();analyser.fftSize=1024;source.connect(analyser);const values=new Float32Array(analyser.fftSize);const update=()=>{if(token!==generation||!analyser)return;analyser.getFloatTimeDomainData(values);let energy=0;for(const sample of values)energy+=sample*sample;callbacks.onLevel?.(Math.min(1,Math.sqrt(energy/values.length)*4));frame=requestAnimationFrame(update)};frame=requestAnimationFrame(update)}catch{callbacks.onLevel?.(0)}
   if(token!==generation){cleanup();return}started=performance.now();local.start(250);recording=true;starting=false;callbacks.onState?.('LISTENING');timer=setTimeout(()=>{if(local.state!=='inactive')local.stop()},60000);
  }catch(error){starting=false;cleanup();if(token===generation){callbacks.onState?.('ERROR');callbacks.onError?.(microphoneError(error));throw new Error(microphoneError(error))}}
 }
 function stop():Promise<VoiceRecording>{if(completed)return Promise.resolve(completed);if(problem)return Promise.reject(problem);if(pending)return Promise.reject(new Error('Запись уже останавливается.'));if(!recorder||recorder.state==='inactive')return Promise.reject(new Error('Запись не начата.'));return new Promise((resolve,reject)=>{pending={resolve,reject};recorder!.stop()})}
 return {start,stop,cancel,active:()=>recording||starting};
}

type SpeechResult={isFinal:boolean;0:{transcript:string};length:number};
type RecognitionEvent={resultIndex:number;results:{length:number;[index:number]:SpeechResult}};
type Recognition={lang:string;continuous:boolean;interimResults:boolean;onresult:((event:RecognitionEvent)=>void)|null;onerror:((event:{error:string})=>void)|null;onstart:(()=>void)|null;onend:(()=>void)|null;start:()=>void;stop:()=>void;abort:()=>void};
type RecognitionConstructor=new()=>Recognition;
function recognitionConstructor():RecognitionConstructor|undefined{if(typeof window==='undefined')return;const api=window as unknown as {SpeechRecognition?:RecognitionConstructor;webkitSpeechRecognition?:RecognitionConstructor};return api.SpeechRecognition??api.webkitSpeechRecognition}
export function browserSTTSupported(){return !!recognitionConstructor()}
export type STTAdapter={start:()=>void;stop:()=>void;cancel:()=>void;active:()=>boolean};
/** Browser Web Speech may process speech remotely. Its use requires separate explicit consent. */
export function createBrowserSTT(options:{remoteConsent:boolean;onTranscript:(text:string,final:boolean)=>void;onState?:(state:AgentState)=>void;onError?:(message:string)=>void;onEnd?:()=>void;silenceIsNormal?:boolean}):STTAdapter{
 const Constructor=recognitionConstructor();if(!Constructor)throw new Error('Распознавание речи недоступно. Можно записать голос и скачать запись.');if(!options.remoteConsent)throw new Error('Разрешите браузерное распознавание: речь может обрабатываться сервисом браузера.');
 const recognition=new Constructor();recognition.lang='ru-RU';recognition.continuous=false;recognition.interimResults=true;let running=false,closed=false,finalText='',timer:ReturnType<typeof setTimeout>|undefined;
 const clearTimer=()=>{if(timer)clearTimeout(timer);timer=undefined};
 recognition.onstart=()=>{if(closed)return;running=true;options.onState?.('LISTENING')};
 recognition.onresult=event=>{if(closed)return;let interim='';for(let i=event.resultIndex;i<event.results.length;i++){const result=event.results[i],text=result[0]?.transcript??'';if(result.isFinal)finalText+=(finalText?' ':'')+text;else interim+=text}options.onTranscript(`${finalText}${interim?(finalText?' ':'')+interim:''}`,!interim);};
 recognition.onerror=event=>{if(closed)return;running=false;clearTimer();if(event.error==='no-speech'&&options.silenceIsNormal)return;const messages:Record<string,string>={'not-allowed':'Браузер запретил распознавание речи.','service-not-allowed':'Сервис распознавания недоступен.','no-speech':'Речь не обнаружена.','network':'Ошибка соединения с сервисом распознавания.','audio-capture':'Микрофон недоступен.','aborted':'Распознавание отменено.'};options.onState?.('ERROR');options.onError?.(messages[event.error]??`Ошибка распознавания: ${event.error}`)};
 recognition.onend=()=>{clearTimer();running=false;if(!closed){options.onState?.('IDLE');options.onEnd?.()}};
 return {start(){if(closed)throw new Error('Сеанс распознавания закрыт.');if(running)return;finalText='';recognition.start();running=true;timer=setTimeout(()=>recognition.stop(),60000)},stop(){if(!running)return;options.onState?.('TRANSCRIBING');recognition.stop()},cancel(){closed=true;clearTimer();running=false;recognition.onresult=null;recognition.onerror=null;recognition.onstart=null;recognition.onend=null;recognition.abort()},active:()=>running};
}
/** Live conversation input. Audio is analysed locally and not saved as a voice message.
 * Web Speech has its own consent; cancel also covers a late getUserMedia permission. */
export function createConversationInput(options:{remoteConsent:boolean;onTranscript:(text:string)=>void;onEnd:()=>void;onError:(message:string)=>void;onLevel:(value:number)=>void}){
 let closed=false,stream:MediaStream|undefined,context:AudioContext|undefined,source:MediaStreamAudioSourceNode|undefined,analyser:AnalyserNode|undefined,stt:STTAdapter|undefined,frame=0;
 function cancel(){closed=true;stt?.cancel();stt=undefined;if(frame)cancelAnimationFrame(frame);frame=0;source?.disconnect();analyser?.disconnect();stream?.getTracks().forEach(t=>t.stop());stream=undefined;if(context)void context.close().catch(()=>{});context=undefined;options.onLevel(0)}
 async function start(){
  if(!options.remoteConsent)throw Error('Разрешите распознавание речи для голосового разговора.');
  if(!navigator.mediaDevices?.getUserMedia||!browserSTTSupported())throw Error('Голосовой разговор не поддерживается этим браузером. Используйте текстовую команду.');
  try{
   const media=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
   if(closed){media.getTracks().forEach(t=>t.stop());return}stream=media;
   const AudioCtor=window.AudioContext||(window as unknown as {webkitAudioContext?:typeof AudioContext}).webkitAudioContext;
   if(AudioCtor)try{context=new AudioCtor();await context.resume();if(closed)return;source=context.createMediaStreamSource(media);analyser=context.createAnalyser();analyser.fftSize=512;source.connect(analyser);const values=new Float32Array(analyser.fftSize);const tick=()=>{if(closed||!analyser)return;analyser.getFloatTimeDomainData(values);let sum=0;for(const v of values)sum+=v*v;options.onLevel(Math.min(1,Math.sqrt(sum/values.length)*5));frame=requestAnimationFrame(tick)};frame=requestAnimationFrame(tick)}catch{options.onLevel(0)}
   if(closed)return;
   stt=createBrowserSTT({remoteConsent:true,silenceIsNormal:true,onTranscript:options.onTranscript,onEnd:options.onEnd,onError:options.onError});stt.start();
  }catch(error){cancel();throw Error(microphoneError(error))}
 }
 return {start,stop(){stt?.stop()},cancel};
}
export function browserTTSSupported(){return typeof window!=='undefined'&&'speechSynthesis'in window&&typeof SpeechSynthesisUtterance!=='undefined'}
/** Native TTS exposes lifecycle but no audio samples: never claims RMS/audio-reactive output. */
export function speakBrowser(text:string,options:{onState?:(state:AgentState)=>void;onEnd?:()=>void;onBoundary?:()=>void;onError?:(message:string)=>void}={}):{cancel:()=>void}{
 if(!browserTTSSupported())throw new Error('Озвучивание недоступно в этом браузере.');if(!text.trim()||text.length>10000)throw new Error('Для озвучивания нужен текст до 10 000 знаков.');const speech=new SpeechSynthesisUtterance(text);speech.lang='ru-RU';speech.rate=1.;let closed=false;
 const voices=window.speechSynthesis.getVoices(),russian=voices.filter(v=>v.lang.toLowerCase().startsWith('ru'));speech.voice=russian.find(v=>v.localService)||russian[0]||null;
 const detach=()=>{speech.onstart=null;speech.onend=null;speech.onerror=null;speech.onboundary=null};
 const timer=setTimeout(()=>{if(closed)return;closed=true;detach();window.speechSynthesis.cancel();options.onState?.('ERROR');options.onError?.('Браузер не начал озвучивание. Ответ доступен текстом. Продолжите разговор нажатием на микрофон.')},10000);
 speech.onboundary=()=>{if(!closed)options.onBoundary?.()};speech.onstart=()=>{clearTimeout(timer);if(!closed){options.onState?.('SPEAKING');options.onBoundary?.()}};speech.onend=()=>{clearTimeout(timer);if(!closed){closed=true;detach();options.onState?.('SUCCESS');options.onEnd?.()}};speech.onerror=event=>{clearTimeout(timer);if(!closed){closed=true;detach();options.onState?.('ERROR');options.onError?.(`Не удалось воспроизвести ответ: ${event.error}`)}};window.speechSynthesis.resume();window.speechSynthesis.speak(speech);
 return {cancel(){clearTimeout(timer);if(closed)return;closed=true;detach();window.speechSynthesis.cancel();options.onState?.('CANCELLED')}};
}
