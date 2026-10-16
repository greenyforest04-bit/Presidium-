import {test,expect,type Page} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
const base='http://127.0.0.1:5173',hs='http://localhost:8008';
const out='../.cache/auth-browser';
async function sign(page:Page,login:string,password:string,register=false){
 await page.goto('/');
 if(register)await page.getByRole('button',{name:'Создать аккаунт',exact:true}).click();
 else if(await page.getByRole('heading',{name:'Вход в Presidium'}).count()===0)await page.getByRole('button',{name:'Войти',exact:true}).click();
 await page.getByLabel('Логин',{exact:true}).fill(login);
 if(register)await page.getByLabel('Отображаемое имя').fill(login+' Name');
 await page.getByLabel('Пароль',{exact:true}).fill(password);
 await page.getByRole('button',{name:register?'Зарегистрироваться':'Войти',exact:true}).click();
}
async function online(page:Page){await expect(page.locator('.nav-profile')).toBeVisible({timeout:60000});await page.getByRole('button',{name:'Сообщения',exact:true}).click();await expect(page.locator('.matrix-inbox-entry')).toContainText('подключено',{timeout:45000})}
async function session(page:Page){return page.evaluate(()=>fetch('/api/auth/session').then(r=>r.json()))}
async function account(page:Page){await page.locator('.nav-profile').click();await page.getByRole('button',{name:'Аккаунт и устройства',exact:true}).click();await expect(page.getByRole('heading',{name:'Устройства и сеансы',exact:true})).toBeVisible()}
async function logout(page:Page){await account(page);await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('button',{name:'Подтвердить выход',exact:true}).click();await expect(page.getByRole('heading',{name:'Вход в Presidium'})).toBeVisible({timeout:30000})}
async function library(page:Page){await page.getByRole('button',{name:'Капсула',exact:true}).click();await page.locator('.cu-section-nav').getByRole('button',{name:'Библиотека',exact:true}).click()}
async function chat(page:Page,login:string){await page.getByTitle('Новый разговор',{exact:true}).click();await page.getByLabel('Логин собеседника',{exact:true}).fill(login);await page.locator('.space-creation').getByRole('button',{name:'Создать',exact:true}).click();await expect(page.locator('.space-creation')).toHaveCount(0,{timeout:30000});await expect(page.locator('.conversation-header h1')).toContainText(login)}
async function send(page:Page,text:string){await page.getByLabel('Сообщение',{exact:true}).fill(text);await page.getByTitle('Отправить сообщение',{exact:true}).click()}
const bubble=(p:Page,t:string)=>p.locator('.message-line').filter({has:p.locator('.bubble > p',{hasText:t})});

test('real accounts, sessions, E2EE delivery, logout, device revocation and password policy',async({browser,request})=>{
 test.setTimeout(300000);await mkdir(out,{recursive:true});
 const suffix=randomUUID().slice(0,8),alice='auth_alice_'+suffix,bob='auth_bob_'+suffix,password=randomUUID(),next=password+'new';
 const contexts=await Promise.all([browser.newContext(),browser.newContext(),browser.newContext(),browser.newContext()]);
 const [a,b,c,d]=await Promise.all(contexts.map(x=>x.newPage()));const errors:string[]=[];for(const p of [a,b,c,d])p.on('pageerror',e=>errors.push(e.message));
 try{
  await a.goto('/');await expect(a.locator('.auth-card')).toBeVisible();await a.screenshot({path:out+'/welcome-desktop.png'});
  await sign(a,alice,password,true);await online(a);
  await sign(b,bob,password,true);await online(b);
  const sa=await session(a),sb=await session(b);expect(sa.user_id).not.toBe(sb.user_id);expect(sa.device_id).not.toBe(sb.device_id);
  const cookies=await contexts[0].cookies();expect(cookies.find(x=>x.name==='presidium_matrix_token')).toMatchObject({httpOnly:true,sameSite:'Strict'});
  expect(await a.evaluate(()=>Object.values(localStorage).some(v=>v.includes('accessToken')))).toBe(false);
  await sign(c,alice.toUpperCase(),password,true);await expect(c.locator('p[role=alert]')).toContainText('занят');
  await c.getByRole('button',{name:'Уже есть аккаунт — войти'}).click();await c.getByLabel('Пароль',{exact:true}).fill('wrong-password');await c.getByRole('button',{name:'Войти',exact:true}).click();await expect(c.locator('p[role=alert]')).toContainText('Неверный');
  await chat(a,bob);const text='AUTH_REAL_AB_'+suffix;await send(a,text);await expect(bubble(a,text)).toHaveCount(1);
  await expect(b.locator('.matrix-network-row')).toHaveCount(1,{timeout:30000});await b.locator('.matrix-network-row').click();await expect(bubble(b,text)).toHaveCount(1,{timeout:30000});
  const reply='AUTH_REAL_BA_'+suffix;await send(b,reply);await expect(bubble(a,reply)).toHaveCount(1,{timeout:30000});
  const rooms=await (await request.get(hs+'/_matrix/client/v3/joined_rooms',{headers:{Authorization:'Bearer '+sa.access_token}})).json();const room=rooms.joined_rooms[0];
  const event=await bubble(a,text).getAttribute('data-message-id');const wire=await (await request.get(hs+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/event/'+encodeURIComponent(event!),{headers:{Authorization:'Bearer '+sa.access_token}})).json();expect(wire.type).toBe('m.room.encrypted');expect(JSON.stringify(wire)).not.toContain(text);
  await Promise.all([a.reload(),b.reload()]);await online(a);await online(b);await a.locator('.matrix-network-row').click();await expect(bubble(a,text)).toHaveCount(1,{timeout:30000});expect((await session(a)).device_id).toBe(sa.device_id);
  await a.screenshot({path:out+'/alice-conversation.png'});
  const own=await a.evaluate(async uid=>fetch('/api/account?userId='+encodeURIComponent(uid),{headers:{'x-presidium-user-id':uid}}).then(r=>r.json()),sb.user_id);expect(own.profile.userId).toBe(sa.user_id);
  const forgedUpdate=await a.evaluate(async({uid,profile})=>fetch('/api/account',{method:'PATCH',headers:{'Content-Type':'application/json','x-presidium-user-id':uid},body:JSON.stringify({...profile,userId:uid,name:'Alice own revised'})}).then(r=>r.json()),{uid:sb.user_id,profile:own.profile});expect(forgedUpdate.profile.userId).toBe(sa.user_id);
  const bobProfile=await b.evaluate(()=>fetch('/api/account').then(r=>r.json()));expect(bobProfile.profile.name).toBe(bob+' Name');
  const foreign=await request.post(hs+'/_matrix/client/v3/register',{data:{username:'auth_carol_'+suffix,password,auth:{type:'m.login.dummy'}}});expect(foreign.ok()).toBeTruthy();const sc=await foreign.json();
  const denied=await request.get(hs+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/messages?dir=b',{headers:{Authorization:'Bearer '+sc.access_token}});expect(denied.status()).toBe(403);
  await chat(a,'auth_carol_'+suffix);const privateText='ALICE_PRIVATE_'+suffix;await send(a,privateText);await expect(bubble(a,privateText)).toHaveCount(1);
  await library(a);await a.locator('.capsule-page input[type=file]').setInputFiles({name:'alice-private.txt',mimeType:'text/plain',buffer:Buffer.from(privateText)});await expect(a.locator('a.pw-file')).toContainText('alice-private.txt');
  await logout(a);expect((await request.get(hs+'/_matrix/client/v3/account/whoami',{headers:{Authorization:'Bearer '+sa.access_token}})).status()).toBe(401);
  expect((await request.get(base+'/api/account',{headers:{Cookie:'presidium_matrix_token='+sa.access_token}})).status()).toBe(401);
  await a.goBack();await a.goto('/');await expect(a.locator('.auth-card')).toBeVisible();
  await sign(a,bob,password);await online(a);await expect(a.locator('body')).not.toContainText(privateText);await expect(a.locator('.matrix-network-row')).toHaveCount(1);await library(a);await expect(a.locator('a.pw-file')).toHaveCount(0);await logout(a);
  await sign(a,alice,password);await online(a);const restored=await session(a);expect(restored.device_id).toBe(sa.device_id);
  await library(a);await expect(a.locator('a.pw-file')).toContainText('alice-private.txt');const fileText=await a.locator('a.pw-file').evaluate((link:HTMLAnchorElement)=>fetch(link.href).then(r=>r.text()));expect(fileText).toBe(privateText);
  await a.getByRole('button',{name:'Сообщения',exact:true}).click();await a.locator('.matrix-network-row').filter({hasText:bob}).click();await expect(bubble(a,text)).toHaveCount(1,{timeout:30000});await expect(bubble(a,reply)).toHaveCount(1);
  await send(a,'AFTER_REAUTH_'+suffix);await b.getByRole('button',{name:'Сообщения',exact:true}).click();await b.locator('.matrix-network-row').click();await expect(bubble(b,'AFTER_REAUTH_'+suffix)).toHaveCount(1,{timeout:30000});await send(b,'REAUTH_REPLY_'+suffix);await expect(bubble(a,'REAUTH_REPLY_'+suffix)).toHaveCount(1,{timeout:30000});
  await sign(d,alice,password);await online(d);const second=await session(d);expect(second.device_id).not.toBe(restored.device_id);
  await d.locator('.matrix-network-row').filter({hasText:bob}).click();await send(d,'DEVICE_TWO_'+suffix);await expect(bubble(b,'DEVICE_TWO_'+suffix)).toHaveCount(1,{timeout:30000});await send(b,'DEVICE_TWO_REPLY_'+suffix);await expect(bubble(d,'DEVICE_TWO_REPLY_'+suffix)).toHaveCount(1,{timeout:30000});await expect(bubble(a,'DEVICE_TWO_REPLY_'+suffix)).toHaveCount(1,{timeout:30000});
  await account(a);const fingerprint=await a.getByTestId('device-fingerprint').textContent();const published=await (await request.post(hs+'/_matrix/client/v3/keys/query',{headers:{Authorization:'Bearer '+restored.access_token},data:{device_keys:{[restored.user_id]:[restored.device_id]}}})).json();expect(published.device_keys[restored.user_id][restored.device_id].keys['ed25519:'+restored.device_id]).toBe(fingerprint);await a.getByLabel('Текущий пароль',{exact:true}).fill(password);await a.getByRole('button',{name:'Завершить остальные сеансы',exact:true}).click();await expect(a.getByRole('status')).toContainText('Все остальные сеансы завершены');await expect(d.locator('.auth-card')).toBeVisible({timeout:15000});
  expect((await request.get(hs+'/_matrix/client/v3/account/whoami',{headers:{Authorization:'Bearer '+second.access_token}})).status()).toBe(401);
  // A new independent device, then password change revokes it, preserving the current one.
  await sign(d,alice,password);await online(d);const third=await session(d);
  await a.getByLabel('Текущий пароль',{exact:true}).fill(password);await a.getByLabel('Новый пароль',{exact:true}).fill(next);await a.getByRole('button',{name:'Изменить пароль',exact:true}).click();await expect(a.getByRole('status')).toContainText('Пароль изменён');await expect(d.locator('.auth-card')).toBeVisible({timeout:15000});expect((await session(a)).user_id).toBe(sa.user_id);
  expect((await request.get(hs+'/_matrix/client/v3/account/whoami',{headers:{Authorization:'Bearer '+third.access_token}})).status()).toBe(401);
  await a.screenshot({path:out+'/account-devices.png'});await a.setViewportSize({width:390,height:844});await a.screenshot({path:out+'/account-mobile.png'});
  const oldLogin=await request.post(base+'/api/auth/login',{headers:{Origin:base},data:{username:alice,password}});expect(oldLogin.status()).toBe(403);
  const newLogin=await request.post(base+'/api/auth/login',{headers:{Origin:base},data:{username:alice,password:next}});expect(newLogin.ok()).toBeTruthy();
  expect((await request.post(base+'/api/auth/login',{headers:{Origin:'https://evil.invalid'},data:{username:alice,password:next}})).status()).toBe(403);
  expect((await request.post(base+'/_matrix/client/v3/register',{data:{username:'bypass_'+suffix,password:next,auth:{type:'m.login.dummy'}}})).status()).toBe(403);
  expect(errors).toEqual([]);
  await writeFile(out+'/evidence.json',JSON.stringify({users:[sa.user_id,sb.user_id],devices:[sa.device_id,sb.device_id,second.device_id],wireType:wire.type,room,event,reload:true,bidirectional:true,logoutRevokesMatrix:true,historyAfterReauth:true,deviceRevocation:true,passwordRevokesOthers:true,impersonationDenied:true,physicalDeviceTest:false,pageErrors:errors},null,2));
 }finally{for(const ctx of contexts)await ctx.close()}
});
