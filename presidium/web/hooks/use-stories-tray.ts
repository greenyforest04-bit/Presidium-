'use client';
import {useEffect,RefObject} from 'react';
import {clamp,rubberPull,snapTarget,springStep,TRAY_HEIGHT} from '@/lib/tray-physics';
/** Native gesture coordinator. Frame values live outside React/list rendering. */
export function useStoriesTray(ref:RefObject<HTMLElement|null>,enabled:boolean,reduceMotion=false){useEffect(()=>{
 const inbox=ref.current;if(!inbox||!enabled)return;const app=inbox.closest<HTMLElement>('.app'),list=inbox.querySelector<HTMLElement>('.chat-list'),shelf=inbox.querySelector<HTMLElement>('.stories-strip');if(!app||!list||!shelf)return;
 const toggle=inbox.querySelector<HTMLButtonElement>('.tray-toggle');
 const mobile=matchMedia('(max-width:720px)'),reduced=matchMedia('(prefers-reduced-motion:reduce)');let p=1,c=0,frame=0,paintFrame=0,momentum=0,wheelTimer:ReturnType<typeof setTimeout>|undefined,lastScroll=list.scrollTop;
 const gesture={active:false,axis:'',x:0,y:0,lastY:0,lastT:0,velocity:0,pull:0,crossed:false};
 function paint(){app!.style.setProperty('--tray-progress',String(clamp(p)));app!.style.setProperty('--tray-collapse',String(1-clamp(p)));app!.style.setProperty('--tray-height',`${clamp(p)*TRAY_HEIGHT}px`);app!.style.setProperty('--tray-opacity',String(clamp((p-.12)/.6)));app!.style.setProperty('--tray-scale',String(.7+.3*clamp(p)));app!.style.setProperty('--tray-y',`${-15*(1-clamp(p))}px`);app!.style.setProperty('--chrome-progress',String(clamp(c)));app!.style.setProperty('--chrome-open',String(1-clamp(c)));app!.style.setProperty('--island-opacity',String(clamp((.65-p)/.65)));app!.style.setProperty('--logo-x',`${clamp(c)*(inbox!.clientWidth/2-36)}px`);if(mobile.matches)app!.style.setProperty('--nav-height',`calc(${68-18*clamp(c)}px + env(safe-area-inset-bottom,0px))`);else app!.style.removeProperty('--nav-height');app!.style.setProperty('--nav-label-height',`${18*(1-clamp(c))}px`);app!.style.setProperty('--search-height',`${42*(1-clamp(c))}px`);app!.style.setProperty('--header-space',`${12*(1-clamp(c))+5}px`);app!.style.setProperty('--title-gap',`${15*(1-clamp(c))}px`);app!.style.setProperty('--filter-height',`${47*(1-clamp(c))}px`);app!.style.setProperty('--footer-height',`${34*(1-clamp(c))}px`);app!.dataset.trayState=gesture.active?'dragging':p>.99?'expanded':c>.99?'collapsed':'default';shelf!.inert=mobile.matches&&p<.05;const peers=inbox!.querySelector<HTMLElement>('.island-peers');if(peers)peers.inert=!mobile.matches||p>.4;if(toggle){toggle.setAttribute('aria-expanded',String(p>.5));toggle.setAttribute('aria-label',p>.5?'Свернуть истории':'Раскрыть истории');}inbox!.querySelectorAll<HTMLElement>('.search,.list-tabs,.inbox-title>.icon-btn').forEach(el=>el.inert=mobile.matches&&c>.95);}
 function requestPaint(){if(!paintFrame)paintFrame=requestAnimationFrame(()=>{paintFrame=0;paint()})}
 function cancel(){cancelAnimationFrame(frame);cancelAnimationFrame(momentum);frame=momentum=0}
 // Captured gestures need their own momentum; native scrolling cannot resume mid-touch.
 function coast(speed:number){
  if((reduceMotion||reduced.matches)||Math.abs(speed)<60)return;
  let previous=performance.now();
  function step(now:number){
   const dt=Math.min((now-previous)/1000,.032);previous=now;
   const before=list!.scrollTop;
   list!.scrollTop=Math.max(0,before+speed*dt);lastScroll=list!.scrollTop;
   speed*=Math.exp(-5.5*dt);
   if(Math.abs(speed)>12&&Math.abs(list!.scrollTop-before)>.1)momentum=requestAnimationFrame(step);
   else momentum=0;
  }
  momentum=requestAnimationFrame(step);
 }
 function settle(cancelled=false,forced?:number){cancel();gesture.active=false;delete app!.dataset.trayDragging;const targetP=forced!==undefined?forced:list!.scrollTop>1&&p<=0?0:cancelled?(p>=.5?1:0):snapTarget(p,performance.now()-gesture.lastT<120?gesture.velocity:0),targetC=targetP?0:1;if((reduceMotion||reduced.matches)){p=targetP;c=targetC;requestPaint();return}let vp=0,vc=0,previous=performance.now();function step(now:number){const dt=Math.min((now-previous)/1000,.032);previous=now;const ps=springStep(p,vp,targetP,dt),cs=springStep(c,vc,targetC,dt);p=ps.position;vp=ps.velocity;c=cs.position;vc=cs.velocity;paint();if(Math.abs(p-targetP)+Math.abs(c-targetC)+Math.abs(vp)+Math.abs(vc)<.003){p=targetP;c=targetC;paint();frame=0}else frame=requestAnimationFrame(step)}frame=requestAnimationFrame(step)}
 function threshold(){if(p>=.5&&!gesture.crossed){gesture.crossed=true;try{navigator.vibrate?.(8)}catch{}}else if(p<.45)gesture.crossed=false}
 function consume(dy:number){let left=dy;if(dy>0){const scroll=Math.min(left,list!.scrollTop);list!.scrollTop-=scroll;lastScroll=list!.scrollTop;left-=scroll;if(c>0){const used=Math.min(left,c*80);c-=used/80;left-=used}if(left>0&&list!.scrollTop<=1){gesture.pull+=left;p=rubberPull(gesture.pull);left=0}}else{if(p>0){const used=Math.min(-left,p*TRAY_HEIGHT);p-=used/TRAY_HEIGHT;left+=used;gesture.pull=-110*Math.log(Math.max(.001,1-clamp(p)))}if(left<0&&c<1){const used=Math.min(-left,(1-c)*80);c+=used/80;left+=used}}
 threshold();requestPaint();return left;}
 function start(e:TouchEvent){if(!mobile.matches||e.touches.length!==1||(e.target as HTMLElement).closest('input,textarea'))return;cancel();clearTimeout(wheelTimer);gesture.active=true;gesture.axis='';gesture.x=e.touches[0].clientX;gesture.y=gesture.lastY=e.touches[0].clientY;gesture.lastT=performance.now();gesture.velocity=0;gesture.pull=-110*Math.log(Math.max(.001,1-clamp(p)));}
 function move(e:TouchEvent){if(!gesture.active||e.touches.length!==1)return;const t=e.touches[0],dx=t.clientX-gesture.x,dy=t.clientY-gesture.y;if(!gesture.axis){if(Math.max(Math.abs(dx),Math.abs(dy))<6)return;gesture.axis=Math.abs(dx)>Math.abs(dy)?'horizontal':'vertical'}if(gesture.axis==='horizontal')return;const now=performance.now(),delta=t.clientY-gesture.lastY;gesture.velocity=.65*delta/Math.max(1,now-gesture.lastT)*1000+.35*gesture.velocity;gesture.lastY=t.clientY;gesture.lastT=now;const intercept=app!.dataset.trayDragging==='true'||p>0||c<1&&delta<0||delta>0&&list!.scrollTop<=1;if(intercept&&e.cancelable){e.preventDefault();app!.dataset.trayDragging='true';const left=consume(delta);if(Math.abs(left)>.1){list!.scrollTop-=left;lastScroll=list!.scrollTop}}}
 function end(){
  if(!gesture.active)return;
  if(gesture.axis==='vertical'&&app!.dataset.trayDragging==='true'){
   const velocity=performance.now()-gesture.lastT<120?gesture.velocity:0;
   const carry=p<.01&&list!.scrollTop>0;
   settle();
   if(carry)coast(-velocity);
  }else gesture.active=false;
 }
 function abort(){if(gesture.active)settle(true)}
 function wheel(e:WheelEvent){if(!mobile.matches||Math.abs(e.deltaX)>Math.abs(e.deltaY)||(e.target as HTMLElement).closest('input,textarea'))return;const dy=-e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?list!.clientHeight:1);if(!(p>0||dy>0&&list!.scrollTop<=1||dy<0&&c<1))return;e.preventDefault();cancel();gesture.active=true;gesture.pull=-110*Math.log(Math.max(.001,1-clamp(p)));gesture.lastT=performance.now();gesture.velocity=0;app!.dataset.trayDragging='true';const left=consume(dy);if(Math.abs(left)>.1){list!.scrollTop-=left;lastScroll=list!.scrollTop}clearTimeout(wheelTimer);wheelTimer=setTimeout(()=>settle(),130)}
 function scroll(){const top=Math.max(0,list!.scrollTop),delta=top-lastScroll;lastScroll=top;if(!mobile.matches||app!.dataset.trayDragging==='true'||frame||momentum||Math.abs(delta)<1)return;c=clamp(c+delta/100);if(delta>0)p=clamp(p-delta/TRAY_HEIGHT);requestPaint()}
 function key(e:KeyboardEvent){if(e.key==='Home'&&!(e.target as HTMLElement).closest('input,textarea')){list!.scrollTop=0;lastScroll=0;gesture.velocity=0;settle(true,1)}}
 function resize(){cancel();p=1;c=0;gesture.active=false;requestPaint()}
 function toggleTray(){gesture.velocity=0;settle(true,p>.5?0:1)}
 function focus(e:FocusEvent){if((e.target as HTMLElement).closest('.search')){c=0;gesture.velocity=0;settle(true)}}
 app.dataset.trayEnabled='true';paint();toggle?.addEventListener('click',toggleTray);inbox.addEventListener('focusin',focus);inbox.addEventListener('touchstart',start,{passive:true});inbox.addEventListener('touchmove',move,{passive:false});inbox.addEventListener('touchend',end);inbox.addEventListener('touchcancel',abort);inbox.addEventListener('wheel',wheel,{passive:false});list.addEventListener('scroll',scroll,{passive:true});inbox.addEventListener('keydown',key);mobile.addEventListener('change',resize);
 return()=>{toggle?.removeEventListener('click',toggleTray);inbox.removeEventListener('focusin',focus);cancel();cancelAnimationFrame(paintFrame);clearTimeout(wheelTimer);inbox.removeEventListener('touchstart',start);inbox.removeEventListener('touchmove',move);inbox.removeEventListener('touchend',end);inbox.removeEventListener('touchcancel',abort);inbox.removeEventListener('wheel',wheel);list.removeEventListener('scroll',scroll);inbox.removeEventListener('keydown',key);mobile.removeEventListener('change',resize);for(const name of ['--tray-progress','--tray-collapse','--nav-height','--tray-height','--tray-opacity','--tray-scale','--tray-y','--chrome-progress','--chrome-open','--island-opacity','--logo-x','--nav-label-height','--search-height','--header-space','--title-gap','--filter-height','--footer-height'])app.style.removeProperty(name);delete app.dataset.trayEnabled;delete app.dataset.trayDragging;delete app.dataset.trayState;shelf.inert=false;inbox.querySelectorAll<HTMLElement>('.search,.list-tabs,.inbox-title>.icon-btn').forEach(el=>el.inert=false);const nav=app.querySelector<HTMLElement>('.rail');if(nav)nav.inert=false;};
 },[ref,enabled,reduceMotion]);}
