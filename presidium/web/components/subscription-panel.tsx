'use client';
import {useEffect,useState} from 'react';
import {PLANS,canUse,type Subscription,type AIUsage} from '@/lib/entitlements';
export function SubscriptionPanel(){
 const [value,setValue]=useState<{subscription:Subscription;usage:AIUsage}>(),[error,setError]=useState('');
 useEffect(()=>{
  const c=new AbortController();
  void fetch('/api/subscription',{cache:'no-store',signal:c.signal}).then(async r=>{
   const body=await r.json() as {subscription?:Subscription;usage?:AIUsage;error?:string};
   if(!r.ok)throw Error(body.error||'Подписка недоступна.');
   if(!body.subscription||!body.usage||!Object.hasOwn(PLANS,body.subscription.tier))throw Error('Неизвестный тариф.');
   setValue({subscription:body.subscription,usage:body.usage});
  }).catch(e=>{if(!c.signal.aborted)setError(e.message)});
  return()=>c.abort();
 },[]);
 const sub=value?.subscription,tier=sub?.tier||'free';
 return <details className="device-key-card" open><summary>Тариф и квоты {value?`· ${PLANS[tier].title}`:''}</summary>
  {Object.entries(PLANS).map(([key,p])=><p className="pw-row" key={key}><b>{p.title}</b><span>{p.configured===false?'По отдельному договору · не подключён':p.priceRub===0?'Бесплатно':`${p.priceRub} ₽/месяц`}{tier===key?' · ваш текущий тариф':''}</span></p>)}
  {sub&&<p className="account-note">Статус тарифа: <b>{sub.verified?'Подтверждён сервером':'Базовый (Free)'}</b>{sub.periodStart&&sub.periodEnd?` · период с ${new Date(sub.periodStart).toLocaleDateString('ru-RU')} по ${new Date(sub.periodEnd).toLocaleDateString('ru-RU')}`:''}.</p>}
  {sub&&<p className="account-note">Доступные серверные возможности: Чаты и E2EE — доступно · Публикации и истории на сервере — {canUse(tier,'social')?'доступно':'требуется Plus или Business'} · Комиссионка (размещение и сделки) — {canUse(tier,'marketplace')?'доступно':'требуется Plus или Business'} · Бизнес-функции — {canUse(tier,'business')?'доступно':'требуется Business'}.</p>}
  {value&&<p className="account-note">Квоты Capsule AI: {value.usage.daily} из {value.usage.dailyLimit} запросов за день · {value.usage.monthly} из {value.usage.monthlyLimit} за месяц. Зарезервировано бюджета: {(value.usage.reservedMicroUsd/1000000).toFixed(4)} USD из лимита {(value.usage.budgetMicroUsd/1000000).toFixed(2)} USD. Резервирование — это предварительная верхняя оценка до вызова модели, она отличается от фактического потребления токенов у провайдера и не является денежным списанием.</p>}
  <p className="account-note">При истечении платной подписки все ваши данные сохраняются; остаются доступны удаление и управление собственными публикациями/историями, снятие объявлений с продажи и отвязка контакта Matrix. Прямой платёжный шлюз на сайте пока не подключён — тариф активируется только после серверного подтверждения.</p>
  {error&&<p className="people-error" role="status">{error}</p>}
 </details>;
}
