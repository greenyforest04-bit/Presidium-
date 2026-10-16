'use client';
import {useLayoutEffect,type RefObject} from 'react';

/** Measures the selected line of a growing textarea without scrolling the whole page. */
export function useComposerCaret(textarea:RefObject<HTMLTextAreaElement|null>,scroller:RefObject<HTMLDivElement|null>,text:string){
 useLayoutEffect(()=>{
  const field=textarea.current,scroll=scroller.current;if(!field||!scroll)return;
  let frame=0;
  function reveal(){
   cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{
    if(document.activeElement!==field)return;
    const box=scroll!.getBoundingClientRect(),rect=field!.getBoundingClientRect(),style=getComputedStyle(field!);
    if(rect.top>=box.top+12&&rect.bottom<=box.bottom-16)return;
    const mirror=document.createElement('div');
    for(const key of ['fontFamily','fontSize','fontWeight','fontStyle','letterSpacing','lineHeight','textIndent','textTransform','tabSize','paddingTop','paddingBottom','paddingLeft','paddingRight'] as const)mirror.style[key]=style[key];
    Object.assign(mirror.style,{position:'fixed',left:'-10000px',top:'0',width:field!.clientWidth+'px',boxSizing:'border-box',whiteSpace:'pre-wrap',overflowWrap:'break-word',visibility:'hidden',pointerEvents:'none'});
    mirror.appendChild(document.createTextNode(field!.value.slice(0,field!.selectionStart)));
    const caret=document.createElement('span');caret.textContent='\u200b';mirror.appendChild(caret);document.body.appendChild(mirror);
    const top=rect.top+caret.offsetTop,height=parseFloat(style.lineHeight)||24;mirror.remove();
    const shift=top<box.top+12?top-(box.top+12):top+height>box.bottom-16?top+height-(box.bottom-16):0;
    if(shift)scroll!.scrollTop+=shift;
   });
  }
  reveal();for(const event of ['focus','keyup','pointerup','select'])field.addEventListener(event,reveal);
  window.visualViewport?.addEventListener('resize',reveal);
  return()=>{cancelAnimationFrame(frame);for(const event of ['focus','keyup','pointerup','select'])field.removeEventListener(event,reveal);window.visualViewport?.removeEventListener('resize',reveal)};
 },[textarea,scroller,text]);
}
