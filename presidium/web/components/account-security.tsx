'use client';
import {useEffect,useState} from 'react';
import {LogOut,Smartphone,KeyRound} from 'lucide-react';
import {localMatrix,authRequest} from '@/lib/local-matrix';
import {PasswordField} from './auth-boundary';

type Device={device_id:string;display_name?:string;last_seen_ts?:number;current:boolean};
export function AccountSecurity(){
 const [security,setSecurity]=useState<{verified:boolean;signed:boolean;ed25519:string}>();
 const [devices,setDevices]=useState<Device[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[password,setPassword]=useState(''),[next,setNext]=useState(''),[confirm,setConfirm]=useState(false);
 async function reload(){setDevices((await authRequest('devices')).devices)}
 useEffect(()=>{void reload().catch(e=>setError(e.message));void localMatrix.deviceSecurity().then(setSecurity).catch(e=>setError(e.message))},[]);
 async function perform(fn:()=>Promise<void>){if(busy)return;setBusy(true);setError('');setNotice('');try{await fn()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <section className="account-security"><h2>Устройства и сеансы</h2><p className="account-note">Логин: <b>{localMatrix.session?.username}</b><br/>ID аккаунта: <code>{localMatrix.session?.userId}</code></p>
 <p className="account-note">Каждый браузер имеет отдельное Matrix-устройство и ключи E2EE. Между новыми устройствами резервное восстановление ключей и проверка доверия пока не подключены. Статус этого устройства: {security?(security.verified?'проверено':'не верифицировано'):'проверяем…'}. {security&&<>Подпись владельца: {security.signed?'есть':'нет'}.<br/>Отпечаток Ed25519: <code data-testid="device-fingerprint">{security.ed25519}</code></>}</p>
 {devices.map(d=><article className="auth-device" key={d.device_id}><Smartphone size={20}/><div><b>{d.display_name||'Matrix-устройство'}</b><small>{d.device_id}{d.current?' · это устройство':''}</small><small>{d.last_seen_ts?new Date(d.last_seen_ts).toLocaleString('ru-RU'):'Нет данных об активности'}</small></div>{!d.current&&<button className="secondary-btn" disabled={busy||!password} onClick={()=>void perform(async()=>{await authRequest('devices',{current_password:password,device_id:d.device_id});await reload();setPassword('');setNotice('Сеанс устройства отозван на сервере Matrix.')})}>Завершить</button>}</article>)}
 <form className="account-profile-form" onSubmit={e=>{e.preventDefault();void perform(async()=>{await authRequest('password',{current_password:password,new_password:next});setPassword('');setNext('');await reload();setNotice('Пароль изменён. Остальные сеансы завершены; этот сеанс сохранён.')})}}>
 <h2><KeyRound size={20}/>Безопасность аккаунта</h2><PasswordField label="Текущий пароль" value={password} onChange={setPassword} disabled={busy}/><PasswordField label="Новый пароль" value={next} onChange={setNext} autoComplete="new-password" minLength={12} disabled={busy}/><p className="account-note">Новый пароль — от 12 символов. Смена пароля завершает остальные сеансы. Для завершения устройств достаточно текущего пароля.</p>
 <button className="primary-btn" disabled={busy||!password||next.length<12}>{busy?'Выполняем…':'Изменить пароль'}</button><button type="button" className="secondary-btn" disabled={busy||!password||!devices.some(d=>!d.current)} onClick={()=>void perform(async()=>{await authRequest('devices',{current_password:password});await reload();setPassword('');setNotice('Все остальные сеансы завершены.')})}>Завершить остальные сеансы</button></form>
 {error&&<p className="people-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <button className="pw-danger auth-logout" disabled={busy} onClick={()=>setConfirm(true)}><LogOut size={19}/>Выйти</button>{confirm&&<div className="people-confirm"><h3>Выйти из аккаунта?</h3><p>Сеанс будет отозван на сервере. Локальные заметки и ключи останутся привязаны к этому аккаунту и браузеру. На новом устройстве старую зашифрованную переписку нельзя восстановить без ключей: резервное восстановление пока не подключено. Не очищайте данные браузера, если они нужны.</p><button className="secondary-btn" disabled={busy} onClick={()=>setConfirm(false)}>Остаться</button><button className="pw-danger" disabled={busy} onClick={()=>void perform(async()=>{await localMatrix.logout()})}>{busy?'Выходим…':'Подтвердить выход'}</button></div>}
 </section>;
}
