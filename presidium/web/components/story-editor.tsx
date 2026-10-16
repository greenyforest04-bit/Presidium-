'use client';
import {useEffect,useImperativeHandle,useRef,useState} from 'react';
import type {Ref} from 'react';
import {Camera,SwitchCamera,X,Type,Music2,BarChart3,Grid2X2,ImagePlus,Check,ArrowRight,ArrowLeft,Trash2,Play,Pause,Smile,Bookmark,Pencil,Loader2,RotateCcw} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/components/ui/dialog';
import {Story,StoryAsset,StoryLayer,FeedPost,recordId,read,write,removeBlob} from '@/lib/presidium';
import {StoryCameraSession,bindStoryCamera,cameraError,recordingOptions,inspectMedia,prepareStoryAssets,boundedMusic,storyProblem,storyBlobIds,STORY_MAX_BYTES,STORY_MAX_SECONDS} from '@/lib/story-media';
import type {CameraSide,CaptureMode} from '@/lib/story-media';
import {StoryScene,useStoryAsset} from '@/components/story-scene';
import {toast} from 'sonner';

type Draft={story:Story;owned:string[]};
type Panel='text'|'poll'|'music'|'stickers'|'layout'|null;
export type StoryStudioHandle={requestClose:()=>void};
const DRAFT_KEY='story-draft-v2';
const colors=['#ffffff','#18181b','#ffe074','#ff7e8d','#81caff','#83d9b4'];
const backgrounds=['#17171c','#34343b','#224239','#58272f','#143954'];
const stickers=['❤️','🔥','✊','✨','☀️','☕','🎉','👍','🫶','📍','💯','🎵','🌊','🌙','💬','✅'];

export function StoryStudio({name,repost,onSave,onClose,ref}:{name:string;repost?:FeedPost;onSave:(story:Story,clearDraft:boolean)=>Promise<void>;onClose:()=>void;ref?:Ref<StoryStudioHandle>}){
 const [phase,setPhase]=useState<'capture'|'edit'|'preview'>(repost?'edit':'capture');
 const [media,setMedia]=useState<StoryAsset[]>(repost?.blobId?[{id:repost.blobId,kind:repost.video?'video':'image'}]:[]);
 const [layers,setLayers]=useState<StoryLayer[]>(repost?.text?[{id:recordId(),kind:'text',text:repost.text,x:50,y:48,size:6,rotation:0,color:'#fff',plate:true}]:[]);
 const [background,setBackground]=useState('#17171c'),[layout,setLayout]=useState<Story['layout']>('grid');
 const [poll,setPoll]=useState<Story['poll']>(),[music,setMusic]=useState<Story['music']>(),[source,setSource]=useState<Story['repost']>(repost?{id:repost.id,author:repost.author}:undefined);
 const [panel,setPanel]=useState<Panel>(null),[selected,setSelected]=useState(''),[mode,setMode]=useState<CaptureMode>('photo');
 const [facing,setFacing]=useState<CameraSide>('user'),[camera,setCamera]=useState(false),[requesting,setRequesting]=useState(false),[error,setError]=useState('');
 const [streamRevision,setStreamRevision]=useState(0),[cameraReady,setCameraReady]=useState(false);
 const [recording,setRecording]=useState(false),[seconds,setSeconds]=useState(0),[busy,setBusy]=useState(false),[progress,setProgress]=useState(0);
 const [previewMusic,setPreviewMusic]=useState(false),[exit,setExit]=useState(false),[stored,setStored]=useState<Draft|null>(null),[draftReady,setDraftReady]=useState(false);
 const live=useRef<HTMLVideoElement>(null),session=useRef<StoryCameraSession|null>(null),recorder=useRef<MediaRecorder|null>(null);
 const alive=useRef(true),cameraRequest=useRef(0),inFlight=useRef(false),owned=useRef(new Set<string>()),restored=useRef(false),duration=useRef(0);
 const gallery=useRef<HTMLInputElement>(null),collage=useRef<HTMLInputElement>(null),audioInput=useRef<HTMLInputElement>(null),audio=useRef<HTMLAudioElement>(null);
 const musicUrl=useStoryAsset(music?.id);
 const currentLayer=layers.find(layer=>layer.id===selected);
 const draft:Story={id:'draft',name,text:layers.filter(l=>l.kind==='text').map(l=>l.text).join('\n'),layers,media,background,layout,poll,music,repost:source,color:'mine',createdAt:0,duration:media.find(a=>a.kind==='video')?.duration||music?.length||8};
 const hasContent=!!(media.length||layers.some(l=>l.text.trim())||poll||music);

 function stopCamera(){cameraRequest.current++;session.current?.stop();setCamera(false);setCameraReady(false);setRequesting(false);}
 async function openCamera(side:CameraSide=facing,nextMode:CaptureMode=mode){
  if(recording||inFlight.current)return;
  const request=++cameraRequest.current;setRequesting(true);setCamera(false);setCameraReady(false);setError('');setPhase('capture');setFacing(side);setMode(nextMode);
  session.current??=new StoryCameraSession();
  try {const stream=await session.current.open(side,nextMode);if(!alive.current||request!==cameraRequest.current||!stream)return;
   const actual=stream.getVideoTracks()[0]?.getSettings().facingMode;setFacing(actual==='user'||actual==='environment'?actual:side);setStreamRevision(n=>n+1);setCamera(true);
  }catch(e){if(alive.current&&request===cameraRequest.current)setError(cameraError(e));}
  finally {if(alive.current&&request===cameraRequest.current)setRequesting(false);}
 }
 useEffect(()=>{
  alive.current=true;
  read<Draft>('state',DRAFT_KEY).then(value=>{if(alive.current){if(value?.story&&Array.isArray(value.owned))setStored(value);setDraftReady(true);}}).catch(()=>{if(alive.current)setDraftReady(true);});
  if(!repost)void openCamera('user','photo');
  function hidden(){if(document.hidden){if(recorder.current?.state==='recording')recorder.current.stop();else stopCamera();}}
  document.addEventListener('visibilitychange',hidden);
  return()=>{alive.current=false;cameraRequest.current++;document.removeEventListener('visibilitychange',hidden);const r=recorder.current;if(r){r.onstop=null;r.ondataavailable=null;r.onerror=null;if(r.state==='recording')r.stop();}session.current?.stop();};
 },[]);
 useEffect(()=>{if(camera&&live.current&&session.current?.stream)return bindStoryCamera(live.current,session.current.stream,()=>setCameraReady(true),()=>setError('Нажмите на кадр, чтобы включить камеру.'));},[camera,streamRevision]);
 useEffect(()=>{if(!recording)return;const start=performance.now();const timer=setInterval(()=>{const value=(performance.now()-start)/1000;duration.current=value;setSeconds(Math.floor(value));if(value>=STORY_MAX_SECONDS&&recorder.current?.state==='recording')recorder.current.stop();},200);return()=>clearInterval(timer);},[recording]);
 useEffect(()=>{if(panel!=='music'){audio.current?.pause();setPreviewMusic(false);}},[panel]);
 useImperativeHandle(ref,()=>({requestClose}));

 async function storeAsset(blob:Blob,kind:'image'|'video',extra:Partial<StoryAsset>={}){
  if(!blob.size)throw Error('Файл пуст. Повторите съёмку.');
  if(blob.size>STORY_MAX_BYTES)throw Error('Выберите файл до 25 МБ.');
  const id=recordId();await write('blobs',id,blob);
  if(!alive.current){await removeBlob(id);throw Error('Редактор закрыт.');}
  owned.current.add(id);return {id,kind,...extra};
 }
 async function snapshot(){
  const video=live.current;if(!cameraReady||!video?.videoWidth||!video.videoHeight||video.srcObject!==session.current?.stream)throw Error('Камера ещё не готова. Попробуйте через секунду.');
  const canvas=document.createElement('canvas'),scale=Math.min(1,1920/Math.max(video.videoWidth,video.videoHeight));
  canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
  const context=canvas.getContext('2d');if(!context)throw Error('Не удалось получить кадр.');
  if(facing==='user'){context.translate(canvas.width,0);context.scale(-1,1);}
  context.drawImage(video,0,0,canvas.width,canvas.height);
  const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(Error('Не удалось сохранить кадр.')),'image/jpeg',.92));
  return storeAsset(blob,'image');
 }
 async function takePhoto(){if(inFlight.current)return;inFlight.current=true;setBusy(true);try{const asset=await snapshot();if(alive.current){setMedia([asset]);stopCamera();setPhase('edit');}}catch(e){toast.error(cameraError(e));}finally{inFlight.current=false;if(alive.current)setBusy(false);}}
 function record(){
  if(recording){recorder.current?.stop();return;}
  const stream=session.current?.stream;
  if(!stream||!cameraReady||live.current?.srcObject!==stream||inFlight.current)return;
  try {
   if(!window.MediaRecorder)throw Error('Запись не поддерживается. Загрузите готовое видео.');
   const r=new MediaRecorder(stream,recordingOptions()),chunks:Blob[]=[];let bytes=0,failed=false;const mirrored=facing==='user';recorder.current=r;
   r.ondataavailable=e=>{if(e.data.size){chunks.push(e.data);bytes+=e.data.size;if(bytes>=24*1024*1024&&r.state==='recording')r.stop();}};
   r.onerror=()=>{failed=true;setError('Запись прервана. Попробуйте снять видео ещё раз.');setRecording(false);if(r.state==='recording')r.stop();stopCamera();};
   r.onstop=async()=>{
    recorder.current=null;if(!alive.current)return;setRecording(false);stopCamera();if(failed)return;
    inFlight.current=true;setBusy(true);
    try {const asset=await storeAsset(new Blob(chunks,{type:r.mimeType||chunks[0]?.type||'video/webm'}),'video',{mirrored,duration:Math.max(1,Math.min(60,duration.current))});if(alive.current){setMedia([asset]);setPhase('edit');}}
    catch(e){toast.error(cameraError(e));}finally{inFlight.current=false;if(alive.current)setBusy(false);}
   };
   duration.current=0;setSeconds(0);r.start(250);setRecording(true);
  }catch(e){setError(cameraError(e));}
 }
 async function importFiles(files:FileList|null,isCollage=false){
  if(!files?.length||inFlight.current)return;
  const selectedFiles=Array.from(files);
  if(isCollage&&(selectedFiles.length<2||selectedFiles.length>4)){toast.error('Для коллажа выберите от 2 до 4 фото.');return;}
  if(selectedFiles.some(f=>f.size>STORY_MAX_BYTES)){toast.error('Каждый файл должен быть не больше 25 МБ.');return;}
  inFlight.current=true;setBusy(true);setProgress(0);stopCamera();const staged:StoryAsset[]=[];
  try {
   for(const [i,file] of selectedFiles.slice(0,isCollage?4:1).entries()){
    const kind=file.type.startsWith('image/')?'image':file.type.startsWith('video/')&&!isCollage?'video':null;
    if(!kind)throw Error(isCollage?'Для коллажа нужны фотографии.':'Выберите фото или видео.');
    const info=await inspectMedia(file,kind);
    if(kind==='video'&&info.duration>STORY_MAX_SECONDS+.2)throw Error('Видео длиннее 60 секунд. Выберите короткий фрагмент.');
    staged.push(await storeAsset(file,kind,{duration:info.duration||undefined}));if(alive.current)setProgress(Math.round((i+1)/selectedFiles.length*100));
   }
   if(alive.current){setMedia(staged);setPhase('edit');if(isCollage)setPanel('layout');}
  }catch(e){await Promise.all(staged.map(a=>removeBlob(a.id).then(()=>owned.current.delete(a.id)).catch(()=>{})));toast.error(cameraError(e));}
  finally {inFlight.current=false;if(alive.current){setBusy(false);setProgress(0);}}
 }
 function updateLayer(id:string,patch:Partial<StoryLayer>){setLayers(current=>current.map(l=>l.id===id?{...l,...patch}:l));}
 function addLayer(kind:StoryLayer['kind'],text=''){
  if(layers.length>=12){toast.error('В истории может быть до 12 слоёв.');return;}
  const layer:StoryLayer={id:recordId(),kind,text,x:50,y:45,size:kind==='sticker'?14:7,rotation:0,color:'#ffffff',plate:false};
  setLayers(current=>[...current,layer]);setSelected(layer.id);if(kind==='text')setPanel('text');else setPanel(null);
 }
 async function tool(next:Panel){
  if(recording||inFlight.current)return;
  if(camera){inFlight.current=true;setBusy(true);try{setMedia([await snapshot()]);}catch(e){toast.error(cameraError(e));return;}finally{inFlight.current=false;setBusy(false);}}
  stopCamera();setPhase('edit');
  if(next==='text')addLayer('text');else{if(next==='poll'&&!poll)setPoll({question:'',options:['Да','Нет']});setPanel(next);}
 }
 async function importMusic(file?:File){
  if(!file||inFlight.current)return;
  if(!file.type.startsWith('audio/')||file.size>STORY_MAX_BYTES){toast.error('Выберите аудиофайл до 25 МБ.');return;}
  inFlight.current=true;setBusy(true);
  try{const info=await inspectMedia(file,'audio');const id=recordId();await write('blobs',id,file);if(!alive.current){await removeBlob(id);return;}owned.current.add(id);setMusic({id,name:file.name.replace(/\.[^.]+$/,''),start:0,length:Math.min(15,info.duration),duration:info.duration});}
  catch(e){toast.error(cameraError(e));}finally{inFlight.current=false;if(alive.current)setBusy(false);}
 }
 function musicRange(start:number,length:number){if(!music)return;audio.current?.pause();setPreviewMusic(false);setMusic({...music,...boundedMusic(start,length,music.duration||0)});}
 async function cleanupUnused(keep:string[]=[]){const keys=[...owned.current].filter(id=>!keep.includes(id));await Promise.all(keys.map(id=>removeBlob(id).then(()=>owned.current.delete(id)).catch(()=>{})));}
 async function saveDraft(){
  if(inFlight.current||!draftReady)return;inFlight.current=true;setBusy(true);stopCamera();
  try {const keep=storyBlobIds(draft);await write('state',DRAFT_KEY,{story:draft,owned:[...owned.current].filter(id=>keep.includes(id))});await cleanupUnused(keep);
   if(stored)await Promise.all(stored.owned.filter(id=>!keep.includes(id)).map(id=>removeBlob(id).catch(()=>{})));
   toast.success('Черновик сохранён');onClose();
  }catch{toast.error('Не удалось сохранить черновик. Освободите память и повторите.');}finally{inFlight.current=false;if(alive.current)setBusy(false);}
 }
 function resume(){if(!stored)return;stopCamera();setMedia(stored.story.media||[]);setLayers(stored.story.layers||[]);setBackground(stored.story.background||'#17171c');setLayout(stored.story.layout||'grid');setMusic(stored.story.music);setPoll(stored.story.poll);setSource(stored.story.repost);owned.current=new Set(stored.owned);restored.current=true;setStored(null);setPhase('edit');}
 function requestClose(){if(busy||inFlight.current)return;if(recording){recorder.current?.stop();return;}if(panel){setPanel(null);return;}if(phase==='preview'){setPhase('edit');return;}if(phase==='capture'&&hasContent){stopCamera();setPhase('edit');return;}stopCamera();if(hasContent||owned.current.size)setExit(true);else onClose();}
 async function discard(){if(inFlight.current)return;inFlight.current=true;setBusy(true);try{if(restored.current)await write('state',DRAFT_KEY,null);await cleanupUnused();onClose();}catch{toast.error('Не удалось удалить черновик.');}finally{inFlight.current=false;if(alive.current)setBusy(false);}}
 async function publish(){
  const problem=storyProblem(draft);if(problem){toast.error(problem);return;}if(inFlight.current)return;
  inFlight.current=true;setBusy(true);stopCamera();const keep=storyBlobIds(draft);
  try{const prepared=await prepareStoryAssets(draft,id=>read<Blob>('blobs',id));await onSave({...prepared,id:recordId(),createdAt:Date.now()},restored.current);await cleanupUnused(keep);}
  catch{toast.error('История не сохранена. Проверьте свободную память и повторите.');}
  finally{inFlight.current=false;if(alive.current)setBusy(false);}
 }
 async function preview(){
  const problem=storyProblem(draft);if(problem){toast.error(problem);return;}if(inFlight.current)return;
  inFlight.current=true;setBusy(true);
  try{const prepared=await prepareStoryAssets(draft,id=>read<Blob>('blobs',id));if(alive.current){setMedia(prepared.media||[]);setSelected('');setPhase('preview');}}
  catch(e){toast.error(cameraError(e));}
  finally{inFlight.current=false;if(alive.current)setBusy(false);}
 }

 return <div className="story-studio story-studio-v2" data-phase={phase} aria-busy={busy}>
  <DialogTitle className="sr-only">Создание истории</DialogTitle><DialogDescription className="sr-only">Камера, редактор и предпросмотр истории.</DialogDescription>
  <div className="studio-stage">
   {phase==='capture'?<><video ref={live} autoPlay playsInline muted className={facing==='user'?'selfie':''} hidden={!camera} onClick={()=>void live.current?.play().catch(()=>{})}/>{!camera&&<div className="studio-start" role="status">{requesting?<Loader2 className="studio-spinner" size={38}/>:<Camera size={40}/>}<p>{requesting?'Подключаем камеру…':error||'Камера на паузе'}</p>{!requesting&&<button onClick={()=>openCamera()}>Включить камеру</button>}<button className="studio-gallery-link" onClick={()=>gallery.current?.click()} disabled={busy}>Выбрать из галереи</button></div>}</>:<StoryScene story={draft} paused={phase!=='preview'||!!panel||exit} editable={phase==='edit'} selected={selected} onSelectLayer={setSelected} onLayerChange={updateLayer}/>}
  </div>
  <header className="studio-header"><button aria-label={phase==='preview'?'Вернуться к редактору':'Закрыть редактор'} disabled={busy} onClick={requestClose}>{phase==='preview'?<ArrowLeft/>:<X/>}</button><span>{recording?<b className="recording-dot">{String(Math.floor(seconds/60)).padStart(2,'0')}:{String(seconds%60).padStart(2,'0')}</b>:phase==='preview'?'Предпросмотр':'История'}</span>{phase==='capture'?<button title="Переключить камеру" aria-label="Переключить камеру" disabled={recording||busy||requesting} onClick={()=>openCamera(facing==='user'?'environment':'user')}><SwitchCamera/></button>:<button title="Сохранить черновик" aria-label="Сохранить черновик" disabled={busy||!draftReady} onClick={saveDraft}><Bookmark/></button>}</header>
  {phase!=='preview'&&!recording&&<aside className="studio-tools" aria-label="Инструменты истории">
   {([[Type,'Добавить текст','text'],[Smile,'Стикеры','stickers'],[BarChart3,'Опрос','poll'],[Music2,'Музыка','music']] as const).map(([Icon,label,next])=><button key={next} title={label} aria-label={label} disabled={busy||requesting||(camera&&!cameraReady)} onClick={()=>tool(next)}><Icon/></button>)}
   <button title="Коллаж" aria-label="Создать коллаж" disabled={busy} onClick={()=>media.length>1?setPanel('layout'):collage.current?.click()}><Grid2X2/></button>
  </aside>}
  {stored&&phase==='capture'&&<button className="studio-resume" disabled={busy||recording} onClick={resume}><Bookmark size={16}/>Продолжить черновик</button>}
  {phase==='edit'&&currentLayer&&<div className="studio-layer-actions"><button onClick={()=>setPanel('text')}><Pencil size={16}/>Изменить</button><button aria-label="Удалить выбранный слой" onClick={()=>{setLayers(ls=>ls.filter(l=>l.id!==selected));setSelected('');}}><Trash2 size={18}/></button><button aria-label="Снять выделение" onClick={()=>setSelected('')}><Check size={18}/></button></div>}
  <footer className="studio-bottom">
   {phase==='capture'?<><div className="studio-mode"><button aria-pressed={mode==='photo'} disabled={recording||busy||requesting} onClick={()=>openCamera(facing,'photo')}>Фото</button><button aria-pressed={mode==='video'} disabled={recording||busy||requesting} onClick={()=>openCamera(facing,'video')}>Видео</button><button disabled={recording||busy} onClick={()=>{stopCamera();setPhase('edit');addLayer('text');}}>Текст</button></div><div className="studio-capture"><button title="Галерея" aria-label="Открыть галерею" onClick={()=>gallery.current?.click()} disabled={recording||busy}><ImagePlus/></button><button className={`shutter ${recording?'is-recording':''}`} aria-label={recording?'Остановить запись':mode==='photo'?'Сделать фото':'Записать видео'} onClick={()=>camera?(mode==='photo'?void takePhoto():record()):void openCamera()} disabled={busy||requesting||(camera&&!cameraReady)}><span/></button><button aria-label="Вернуться к редактированию" disabled={!hasContent||recording||busy} onClick={()=>{stopCamera();setPhase('edit');}}><ArrowRight/></button></div></>:<div className="studio-edit-actions">{phase==='edit'?<><button className="studio-secondary" disabled={busy} onClick={()=>openCamera()}><RotateCcw size={18}/>Переснять</button><button className="studio-primary" disabled={busy||!hasContent} onClick={preview}>Далее<ArrowRight size={19}/></button></>:<><button className="studio-secondary" disabled={busy} onClick={()=>setPhase('edit')}><Pencil size={18}/>Изменить</button><button className="studio-primary" disabled={busy} onClick={publish}>{busy?<Loader2 className="studio-spinner" size={18}/>:<Check size={18}/>}Моя история</button></>}</div>}
   <small>{busy?(progress?`Загрузка ${progress}%`:'Сохраняем…'):phase==='capture'&&camera&&!cameraReady?(error||'Подключаем камеру…'):phase==='capture'&&mode==='video'?'Видео до 60 секунд':'24 часа · на этом устройстве'}</small>
  </footer>
  <input ref={gallery} hidden type="file" accept="image/*,video/*" onChange={e=>{void importFiles(e.target.files);e.target.value='';}}/>
  <input ref={collage} hidden multiple type="file" accept="image/*" onChange={e=>{void importFiles(e.target.files,true);e.target.value='';}}/>
  <input ref={audioInput} hidden type="file" accept="audio/*" onChange={e=>{void importMusic(e.target.files?.[0]);e.target.value='';}}/>
  <audio ref={audio} src={musicUrl||undefined} onEnded={()=>setPreviewMusic(false)} onTimeUpdate={e=>{if(music&&e.currentTarget.currentTime>=music.start+music.length){e.currentTarget.pause();setPreviewMusic(false);}}}/>
  <Dialog open={!!panel} onOpenChange={open=>{if(!open&&!busy)setPanel(null);}}><DialogContent customPosition className="studio-tool-dialog" showCloseButton={false} onOpenAutoFocus={e=>e.preventDefault()} onEscapeKeyDown={e=>{e.stopPropagation();if(busy)e.preventDefault();}}>
   <header><DialogTitle>{({text:'Текст и стикеры',poll:'Опрос',music:'Музыка',stickers:'Стикеры',layout:'Коллаж'})[panel||'text']}</DialogTitle><button aria-label="Готово" disabled={busy} onClick={()=>setPanel(null)}><Check/></button></header><DialogDescription className="sr-only">Настройка содержимого истории</DialogDescription>
   <div className="studio-tool-body">
   {panel==='text'&&currentLayer&&<>
    {currentLayer.kind==='text'&&<textarea aria-label="Текст слоя" maxLength={300} rows={3} placeholder="Ваш текст" value={currentLayer.text} onChange={e=>updateLayer(selected,{text:e.target.value})}/>}
    <label>Размер<input aria-label="Размер слоя" type="range" min={currentLayer.kind==='sticker'?8:4} max={currentLayer.kind==='sticker'?28:12} step=".5" value={currentLayer.size} onChange={e=>updateLayer(selected,{size:+e.target.value})}/></label>
    <label>Поворот<input aria-label="Поворот слоя" type="range" min="-45" max="45" value={currentLayer.rotation} onChange={e=>updateLayer(selected,{rotation:+e.target.value})}/></label>
    {currentLayer.kind==='text'&&<><div className="studio-colors">{colors.map(color=><button key={color} aria-label={'Цвет текста '+color} aria-pressed={currentLayer.color===color} style={{background:color}} onClick={()=>updateLayer(selected,{color})}/>)}</div><label className="studio-checkbox"><input type="checkbox" checked={!!currentLayer.plate} onChange={e=>updateLayer(selected,{plate:e.target.checked})}/>Подложка текста</label></>}
    {!media.length&&<><span>Фон</span><div className="studio-colors">{backgrounds.map(color=><button key={color} aria-label={'Фон '+color} aria-pressed={background===color} style={{background:color}} onClick={()=>setBackground(color)}/>)}</div></>}
    <button className="studio-remove" onClick={()=>{setLayers(ls=>ls.filter(l=>l.id!==selected));setSelected('');setPanel(null);}}>Удалить слой</button>
   </>}
   {panel==='stickers'&&<div className="studio-sticker-grid">{stickers.map(sticker=><button key={sticker} aria-label={'Стикер '+sticker} onClick={()=>addLayer('sticker',sticker)}>{sticker}</button>)}</div>}
   {panel==='poll'&&poll&&<><input aria-label="Вопрос опроса" placeholder="Ваш вопрос" maxLength={100} value={poll.question} onChange={e=>setPoll({...poll,question:e.target.value})}/>{poll.options.map((option,i)=><input key={i} aria-label={'Вариант '+(i+1)} value={option} maxLength={40} onChange={e=>setPoll({...poll,options:poll.options.map((o,j)=>i===j?e.target.value:o)})}/>)}<button className="studio-remove" onClick={()=>{setPoll(undefined);setPanel(null);}}>Удалить опрос</button></>}
   {panel==='layout'&&<><div className="studio-layout-options">{(['grid','rows','columns'] as const).map((value,i)=><button key={value} aria-pressed={layout===value} onClick={()=>setLayout(value)}><span className={'layout-swatch '+value}><i/><i/><i/><i/></span>{['Сетка','Ряды','Колонки'][i]}</button>)}</div><button className="studio-file" disabled={busy} onClick={()=>collage.current?.click()}><ImagePlus/>Выбрать 2–4 фотографии</button><button className="studio-remove" onClick={()=>{setMedia([]);setPanel(null);}}>Убрать медиа</button></>}
   {panel==='music'&&<><button className="studio-file" disabled={busy} onClick={()=>audioInput.current?.click()}>{busy?<Loader2 className="studio-spinner"/>:<Music2/>}Выбрать аудиофайл</button>{music&&<><strong className="track-name">{music.name}</strong><div className="studio-track-range"><span>{music.start.toFixed(1)} с</span><span>{(music.start+music.length).toFixed(1)} с</span></div><label>Начало фрагмента<input aria-label="Начало музыки" type="range" min="0" max={Math.max(0,(music.duration||0)-music.length)} step=".1" value={music.start} onChange={e=>musicRange(+e.target.value,music.length)}/></label><label>Длительность · {music.length.toFixed(1)} с<input aria-label="Длительность музыки" type="range" min={Math.min(1,music.duration||1)} max={Math.min(30,music.duration||30)} step=".1" value={music.length} onChange={e=>musicRange(music.start,+e.target.value)}/></label><button className="studio-file" onClick={()=>{const a=audio.current;if(!a)return;if(previewMusic){a.pause();setPreviewMusic(false);}else{a.currentTime=music.start;void a.play().then(()=>setPreviewMusic(true)).catch(()=>toast.error('Не удалось воспроизвести аудио.'));}}}>{previewMusic?<Pause/>:<Play/>}{previewMusic?'Остановить':'Прослушать фрагмент'}</button><button className="studio-remove" onClick={()=>{audio.current?.pause();setMusic(undefined);setPreviewMusic(false);}}>Убрать музыку</button></>}</>}
   </div>
  </DialogContent></Dialog>
  <Dialog open={exit} onOpenChange={value=>!busy&&setExit(value)}><DialogContent customPosition className="studio-exit-dialog" showCloseButton={false} onEscapeKeyDown={e=>{e.stopPropagation();if(busy)e.preventDefault();}}><DialogTitle>Сохранить историю?</DialogTitle><DialogDescription>К черновику можно вернуться позже.</DialogDescription><button className="studio-primary" disabled={busy||!draftReady} onClick={saveDraft}><Bookmark size={18}/>Сохранить черновик</button><button className="studio-remove" disabled={busy} onClick={discard}>Удалить изменения</button><button className="studio-secondary" disabled={busy} onClick={()=>setExit(false)}>Продолжить редактирование</button></DialogContent></Dialog>
 </div>;
}
