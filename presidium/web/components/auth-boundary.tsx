'use client';
import {useEffect,useState,type ReactNode} from 'react';
import {Eye,EyeOff,ArrowLeft,ShieldCheck} from 'lucide-react';
import {localMatrix} from '@/lib/local-matrix';
import {LocalMatrixLogin} from './local-matrix-login';

export function AuthBoundary({children}:{children:ReactNode}){
 const [state,setState]=useState(localMatrix.snapshot()),[ready,setReady]=useState(false),[mode,setMode]=useState<'welcome'|'login'|'register'>('welcome');
 useEffect(()=>{
  let authenticated=false;const off=localMatrix.subscribe(value=>{if(value.session)authenticated=true;else if(authenticated)setMode('login');setState(value)});
  void localMatrix.resume().catch(()=>{localMatrix.clearActive()}).finally(()=>setReady(true));
  const check=()=>{if(document.visibilityState==='visible')void localMatrix.validateSession()};
  window.addEventListener('pageshow',check);document.addEventListener('visibilitychange',check);
  return()=>{off();window.removeEventListener('pageshow',check);document.removeEventListener('visibilitychange',check)};
 },[]);
 if(!ready)return <main className="auth-shell"><p role="status">Восстанавливаем защищённый сеанс…</p></main>;
 if(state.session)return <div key={state.session.userId}>{children}</div>;
 return <main className="auth-shell"><section className="auth-card"><div className="auth-brand"><img src="/presidium-fist.png" alt=""/><span>PRESIDIUM</span></div>{mode==='welcome'?<><h1>Свои люди.<br/>На связи.</h1><p>Личные разговоры со сквозным шифрованием. Один аккаунт — ваши чаты и пространство Presidium.</p><button className="primary-btn" onClick={()=>setMode('login')}>Войти</button><button className="secondary-btn" onClick={()=>setMode('register')}>Создать аккаунт</button><small><ShieldCheck size={16}/>Пароль хранится на сервере Matrix в виде защищённого хеша.</small></>:<><button className="auth-back" onClick={()=>setMode('welcome')}><ArrowLeft size={18}/>Назад</button><LocalMatrixLogin mode={mode} onModeChange={setMode} onConnected={async()=>{setMode('login')}}/></>}</section></main>;
}

export function PasswordField({label,value,onChange,autoComplete='current-password',disabled=false,minLength=1}:{label:string;value:string;onChange:(value:string)=>void;autoComplete?:string;disabled?:boolean;minLength?:number}){
 const [visible,setVisible]=useState(false);
 return <label className="pw-field">{label}<span className="auth-password"><input aria-label={label} required type={visible?'text':'password'} autoComplete={autoComplete} value={value} onChange={e=>onChange(e.target.value)} disabled={disabled} minLength={minLength} maxLength={256}/><button type="button" aria-label={visible?'Скрыть '+label.toLowerCase():'Показать '+label.toLowerCase()} aria-pressed={visible} disabled={disabled} onClick={()=>setVisible(!visible)}>{visible?<EyeOff size={18}/>:<Eye size={18}/>}</button></span></label>;
}
