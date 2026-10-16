'use client';
import {useState} from 'react';
import {Lock} from 'lucide-react';
import {localMatrix} from '@/lib/local-matrix';
import {PasswordField} from './auth-boundary';
export function LocalMatrixLogin({onConnected,mode='login',onModeChange}:{onConnected:()=>Promise<void>;mode?:'login'|'register';onModeChange?:(mode:'login'|'register')=>void}){
 const [username,setUsername]=useState(''),[displayName,setDisplayName]=useState(''),[password,setPassword]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(){if(busy)return;setBusy(true);setError('');try{if(!/^[a-z0-9][a-z0-9._=-]{2,63}$/i.test(username.trim()))throw Error('Проверьте логин: латиница, цифры, . _ = -; 3–64 символа.');const input={baseUrl:window.location.origin,username:username.trim().toLowerCase(),password,displayName:displayName.trim()};if(mode==='register')await localMatrix.register(input);else await localMatrix.connect(input);setPassword('');await onConnected()}catch(e){setError(e instanceof Error?e.message:'Ошибка подключения')}finally{setBusy(false)}}
 return <form className="account-profile-form" onSubmit={event=>{event.preventDefault();void submit()}}>
  <h2>{mode==='register'?'Создать аккаунт':'Вход в Presidium'}</h2><p className="account-note">Один аккаунт для переписки и профиля. Телефон не требуется.</p>
  <label className="pw-field">Логин<input aria-label="Логин" value={username} autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} onChange={e=>setUsername(e.target.value)} required minLength={3} maxLength={64} disabled={busy}/><small>3–64 символа: латиница, цифры, . _ = -; регистр не учитывается.</small></label>
  {mode==='register'&&<label className="pw-field">Отображаемое имя<input aria-label="Отображаемое имя" value={displayName} autoComplete="nickname" onChange={e=>setDisplayName(e.target.value)} required maxLength={80} disabled={busy}/></label>}
  <PasswordField label="Пароль" value={password} onChange={setPassword} autoComplete={mode==='register'?'new-password':'current-password'} disabled={busy} minLength={mode==='register'?12:1}/>
  {error&&<p className="people-error" role="alert">{error}</p>}
  <button className="primary-btn" disabled={busy}><Lock/>{busy?'Подключаемся…':mode==='register'?'Зарегистрироваться':'Войти'}</button>
  {onModeChange&&<button type="button" className="secondary-btn" disabled={busy} onClick={()=>{setError('');setPassword('');onModeChange(mode==='register'?'login':'register')}}>{mode==='register'?'Уже есть аккаунт — войти':'Создать аккаунт'}</button>}
 </form>
}
