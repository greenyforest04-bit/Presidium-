import type {AgentState} from './agent-state';
export type ConversationTurn={question:string;answer:string};
export type ConversationInput={stop:()=>void;cancel:()=>void};
type InputEvents={onTranscript:(text:string)=>void;onEnd:()=>void;onError:(message:string)=>void;onLevel:(value:number)=>void};
export type ConversationOptions={
 openInput:(events:InputEvents,signal:AbortSignal)=>Promise<ConversationInput>;
 respond:(question:string,history:ConversationTurn[],signal:AbortSignal)=>Promise<string>;
 speak:(text:string,events:{onEnd:()=>void;onError:(message:string)=>void})=>{cancel:()=>void};
 onState:(state:AgentState)=>void;onTranscript:(text:string)=>void;onTurn:(turn:ConversationTurn)=>void;
 onError:(message:string)=>void;onLevel:(value:number)=>void;maxTurns?:number;maxDurationMs?:number;
};
/** Half-duplex conversation: the microphone is released BEFORE a model call or TTS.
 * Generations reject late permission/model/playback callbacks; no automatic billable retry. */
export function createVoiceConversation(options:ConversationOptions){
 let enabled=false,muted=false,expired=false,epoch=0,input:ConversationInput|undefined,output:{cancel:()=>void}|undefined;
 let request:AbortController|undefined,transcript='',turns:ConversationTurn[]=[],silent=0;
 let timer:ReturnType<typeof setTimeout>|undefined,stageTimer:ReturnType<typeof setTimeout>|undefined;
 const valid=(token:number)=>enabled&&!muted&&token===epoch;
 const clearStage=()=>{if(stageTimer)clearTimeout(stageTimer);stageTimer=undefined};
 function release(){clearStage();input?.cancel();input=undefined;request?.abort();request=undefined;output?.cancel();output=undefined;options.onLevel(0)}
 function pause(message?:string){if(!enabled)return;muted=true;epoch++;release();options.onState('PAUSED');if(message)options.onError(message)}
 function fail(message:string){if(!enabled)return;muted=true;epoch++;release();options.onState('ERROR');options.onError(message)}
 async function listen(){
  if(!enabled||muted)return;const token=++epoch;release();transcript='';options.onTranscript('');options.onState('LISTENING');
  stageTimer=setTimeout(()=>{if(valid(token))pause('Микрофон на паузе: речь не обнаружена. Нажмите, чтобы продолжить.')},65000);
  const abort=new AbortController();request=abort;
  try{const next=await options.openInput({
   onTranscript:text=>{if(valid(token)){transcript=text.slice(0,800);options.onTranscript(transcript)}},
   onLevel:value=>{if(valid(token))options.onLevel(value)},
   onEnd:()=>{if(valid(token))void finish(token)},
   onError:message=>{if(valid(token))fail(message)},
  },abort.signal);if(!valid(token))next.cancel();else input=next;}catch(error){if(valid(token))fail(error instanceof Error?error.message:'Не удалось включить микрофон.')}
 }
 async function finish(token:number){
  if(!valid(token))return;const text=transcript.trim();const task=++epoch;release();
  if(!text){silent++;if(silent>=3){pause('Речь не обнаружена. Нажмите на микрофон, чтобы продолжить.');return}void listen();return}
  silent=0;if(turns.length>=(options.maxTurns??10)){pause('Лимит реплик этого разговора достигнут. Начните новый разговор.');return}
  options.onState('TRANSCRIBING');const abort=new AbortController();request=abort;
  stageTimer=setTimeout(()=>{if(valid(task))fail('Ответ не получен за 35 секунд. Повторите команду вручную.')},35000);
  try{
   options.onState('THINKING');const answer=await options.respond(text,turns.slice(-3),abort.signal);if(!valid(task)||abort.signal.aborted)return;
   clearStage();request=undefined;const turn={question:text,answer};turns.push(turn);options.onTurn(turn);options.onState('SUCCESS');
   options.onState('SPEAKING');stageTimer=setTimeout(()=>{if(valid(task))fail('Озвучивание остановлено. Ответ доступен текстом.')},120000);
   output=options.speak(answer,{onEnd:()=>{if(valid(task)){clearStage();output=undefined;options.onState('SUCCESS');void listen()}},onError:message=>{if(valid(task))fail(message)}});
  }catch(error){if(valid(task))fail(error instanceof Error?error.message:'Агент не ответил.')}
 }
 return {
  start(){if(enabled)return;enabled=true;muted=false;expired=false;turns=[];silent=0;timer=setTimeout(()=>{expired=true;pause('Разговор на паузе после 10 минут. Начните новый сеанс.')},options.maxDurationMs??600000);void listen()},
  mute(){pause()},
  resume(){if(!enabled||!muted||expired)return;muted=false;options.onError('');void listen()},
  interrupt(){if(!enabled||expired)return;muted=false;epoch++;release();options.onState('CANCELLED');options.onState('IDLE');options.onError('');void listen()},
  send(){if(enabled&&!muted&&input){options.onState('TRANSCRIBING');input.stop()}},
  end(){enabled=false;muted=false;epoch++;release();if(timer)clearTimeout(timer);timer=undefined;options.onState('CANCELLED');options.onState('IDLE')},
  active:()=>enabled,muted:()=>muted,
 };
}
export type VoiceConversation=ReturnType<typeof createVoiceConversation>;
