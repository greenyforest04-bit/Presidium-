'use client';
import {useEffect} from 'react';
/** Uses the visible browser viewport, including the on-screen keyboard. */
export function ViewportFrame(){
 useEffect(()=>{const viewport=window.visualViewport;let frame=0;const update=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{const root=document.documentElement;root.style.setProperty('--app-height',`${viewport?.height??window.innerHeight}px`);root.style.setProperty('--visual-top',`${viewport?.offsetTop??0}px`);root.style.setProperty('--visual-left',`${viewport?.offsetLeft??0}px`);root.style.setProperty('--app-width',`${viewport?.width??window.innerWidth}px`);root.dataset.keyboard=String(!!viewport&&window.innerHeight-viewport.height>140)})};update();viewport?.addEventListener('resize',update);viewport?.addEventListener('scroll',update);window.addEventListener('resize',update);return()=>{cancelAnimationFrame(frame);viewport?.removeEventListener('resize',update);viewport?.removeEventListener('scroll',update);window.removeEventListener('resize',update)}},[]);
 return null;
}
