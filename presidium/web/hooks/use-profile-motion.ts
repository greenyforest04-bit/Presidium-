'use client';
import {useEffect,RefObject} from 'react';
/** Scroll-only CSS variables; no React renders while moving the profile. */
export function useProfileMotion(ref:RefObject<HTMLElement|null>){useEffect(()=>{const el=ref.current;if(!el)return;let frame=0;const paint=()=>{frame=0;const p=Math.min(1,Math.max(0,el.scrollTop/180));el.style.setProperty('--profile-collapse',String(p));el.style.setProperty('--portrait-scale',String(1-p*.38));el.style.setProperty('--portrait-lift',`${p*36}px`)};const scroll=()=>{if(!frame)frame=requestAnimationFrame(paint)};el.addEventListener('scroll',scroll,{passive:true});paint();return()=>{el.removeEventListener('scroll',scroll);cancelAnimationFrame(frame)}},[ref])}
