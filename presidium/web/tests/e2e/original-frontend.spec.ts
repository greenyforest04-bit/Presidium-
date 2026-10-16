import {randomUUID} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {test,expect,type Page} from '@playwright/test';

const hs='http://localhost:8008';
const screenshots='../.cache/frontend-correction-browser';
async function chats(page:Page){await page.getByRole('button',{name:'Сообщения',exact:true}).click()}
async function send(page:Page,text:string){await page.getByLabel('Сообщение',{exact:true}).fill(text);await page.getByTitle('Отправить сообщение',{exact:true}).click()}
const bubble=(page:Page,text:string)=>page.locator('.message-line').filter({has:page.locator('.bubble > p',{hasText:text})});

async function login(page:Page,username:string,password:string){
 await page.goto('/');await page.getByRole('button',{name:'Войти',exact:true}).click();
 await page.getByLabel('Логин',{exact:true}).fill(username);await page.getByLabel('Пароль',{exact:true}).fill(password);await page.getByRole('button',{name:'Войти',exact:true}).click();
 await expect(page.locator('.auth-account-shortcut')).toBeVisible({timeout:45000});await chats(page);await expect(page.locator('.matrix-inbox-entry')).toContainText('подключено');
}
async function signOut(page:Page){await page.locator('.auth-account-shortcut').click();await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('button',{name:'Подтвердить выход',exact:true}).click();await expect(page.getByRole('heading',{name:'Вход в Presidium'})).toBeVisible()}
async function currentSession(page:Page){const s=await page.evaluate(()=>fetch('/api/auth/session').then(r=>r.json()));return {userId:s.user_id,deviceId:s.device_id,accessToken:s.access_token}}
async function registerUI(page:Page,username:string,password:string){
 await page.goto('/');await page.getByRole('button',{name:'Создать аккаунт',exact:true}).click();await page.getByLabel('Логин',{exact:true}).fill(username);await page.getByLabel('Отображаемое имя').fill(username);await page.getByLabel('Пароль',{exact:true}).fill(password);await page.getByRole('button',{name:'Зарегистрироваться',exact:true}).click();await expect(page.locator('.auth-account-shortcut')).toBeVisible({timeout:45000});await chats(page);
}

test('original conversation: real E2EE delivery, isolation, reconnect and preserved device/history',async({browser,request})=>{
 test.setTimeout(240000);await mkdir(screenshots,{recursive:true});
 const suffix=randomUUID().replaceAll('-','').slice(0,8),password=randomUUID();
 const contexts=[],users:Record<string,any>={},pages:Record<string,Page>={},errors:string[]=[];
 try{
  for(const name of ['alice','bob','carol']){
   const username=`original_${name}_${suffix}`;
   const response=await request.post(`${hs}/_matrix/client/v3/register`,{data:{username,password,auth:{type:'m.login.dummy'}}});
   expect(response.ok(),await response.text()).toBeTruthy();users[name]={username,...await response.json()};
   const context=await browser.newContext({viewport:{width:1440,height:1000}});contexts.push(context);
   const page=await context.newPage();pages[name]=page;page.on('pageerror',e=>errors.push(String(e)));
   await login(page,username,password);
   console.log('Original UI authenticated:',name);
  }
  const {alice,bob,carol}=pages;
  async function openChat(page:Page,target:string){
   await page.getByTitle('Новый разговор',{exact:true}).click();
   await page.getByLabel('Логин собеседника',{exact:true}).fill(users[target].user_id);
   await page.locator('.space-creation').getByRole('button',{name:'Создать',exact:true}).click();
   await expect(page.locator('.space-creation')).toHaveCount(0,{timeout:30000});
   await expect(page.locator('.conversation-header h1')).toContainText(users[target].username);
   await expect(page.locator('.conversation-header .subline')).toContainText('Matrix',{timeout:30000});
  }
  await openChat(alice,'bob');const ab=`REAL_AB_${suffix}`;await send(alice,ab);
  await expect(bubble(alice,ab)).toHaveCount(1);
  await expect(bob.locator('.matrix-network-row')).toHaveCount(1,{timeout:30000});await bob.locator('.matrix-network-row').click();
  await expect(bubble(bob,ab)).toHaveCount(1,{timeout:30000});
  const reply=`REAL_REPLY_${suffix}`;await send(bob,reply);await expect(bubble(alice,reply)).toHaveCount(1,{timeout:30000});
  const session=await currentSession(alice);
  const roomId=(await request.get(`${hs}/_matrix/client/v3/joined_rooms`,{headers:{Authorization:`Bearer ${session.accessToken}`}})).json().then(x=>x.joined_rooms[0]);
  const eventId=await bubble(alice,ab).getAttribute('data-message-id');
  const raw=await request.get(`${hs}/_matrix/client/v3/rooms/${encodeURIComponent(await roomId)}/event/${encodeURIComponent(eventId!)}`,{headers:{Authorization:`Bearer ${session.accessToken}`}});
  expect(raw.ok()).toBeTruthy();const encrypted=await raw.json();expect(encrypted.type).toBe('m.room.encrypted');expect(JSON.stringify(encrypted)).not.toContain(ab);
  console.log('Real encrypted request and reply confirmed');
  await alice.screenshot({animations:'disabled',path:`${screenshots}/main-desktop-conversation.png`});
  await alice.setViewportSize({width:390,height:844});await expect(alice.locator('.conversation')).toBeVisible();
  await alice.screenshot({animations:'disabled',path:`${screenshots}/main-mobile-conversation.png`});await alice.setViewportSize({width:1440,height:1000});
  await bob.context().setOffline(true);await send(alice,`AFTER_RECONNECT_${suffix}`);await bob.context().setOffline(false);
  await expect(bubble(bob,`AFTER_RECONNECT_${suffix}`)).toHaveCount(1,{timeout:45000});
  await Promise.all([alice.reload(),bob.reload()]);
  for(const page of [alice,bob]){await chats(page);await expect(page.locator('.matrix-network-row')).toHaveCount(1,{timeout:45000});await page.locator('.matrix-network-row').click();await expect(bubble(page,ab)).toHaveCount(1,{timeout:30000});await expect(bubble(page,reply)).toHaveCount(1)}
  const restored=await currentSession(alice);
  expect(restored.deviceId).toBe(session.deviceId);expect(restored.cryptoDatabasePrefix).toBe(session.cryptoDatabasePrefix);
  console.log('Reload, device and reconnect confirmed');
  await openChat(alice,'carol');const ac=`REAL_AC_${suffix}`;await send(alice,ac);
  await expect(carol.locator('.matrix-network-row')).toHaveCount(1,{timeout:30000});await carol.locator('.matrix-network-row').click();await expect(bubble(carol,ac)).toHaveCount(1,{timeout:30000});
  await expect(bob.locator('body')).not.toContainText(ac);await expect(carol.locator('body')).not.toContainText(ab);
  await expect(alice.locator('.message-line')).not.toContainText(ab);
  // The local content database must not contain a parallel copy of Matrix history.
  const copies=await alice.evaluate(()=>new Promise<any[]>((resolve,reject)=>{const r=indexedDB.open('presidium-local-v1');r.onsuccess=()=>{const q=r.result.transaction('state').objectStore('state').getAll();q.onsuccess=()=>resolve(q.result);q.onerror=reject};r.onerror=reject}));
  expect(JSON.stringify(copies)).not.toContain(ab);expect(JSON.stringify(copies)).not.toContain(ac);
  await alice.getByTitle('Новый разговор',{exact:true}).click();
  await alice.locator('.sc-types').getByRole('button',{name:'Комната',exact:true}).click();
  await alice.getByLabel('Название',{exact:true}).fill(`Group ${suffix}`);await alice.getByLabel('Matrix ID участников',{exact:true}).fill(users.bob.user_id);
  await alice.locator('.space-creation').getByRole('button',{name:'Создать',exact:true}).click();
  await expect(alice.locator('.conversation-header h1')).toHaveText(`Group ${suffix}`);
  const groupRow=bob.locator('.matrix-network-row').filter({hasText:`Group ${suffix}`});await expect(groupRow).toHaveCount(1,{timeout:30000});await groupRow.click();
  const group=`REAL_GROUP_${suffix}`;await send(alice,group);await expect(bubble(bob,group)).toHaveCount(1,{timeout:30000});await expect(carol.locator('body')).not.toContainText(group);
  // Logout revokes the transport token, then the new login restores the preserved keys.
  await signOut(alice);await login(alice,users.bob.username,password);
  await expect(alice.locator('body')).not.toContainText(ac);
  await signOut(alice);await login(alice,users.alice.username,password);
  await alice.locator('.matrix-network-row').filter({hasText:users.carol.username}).click();await expect(bubble(alice,ac)).toHaveCount(1,{timeout:30000});
  const finalSession=await currentSession(alice);
  expect(finalSession.deviceId).toBe(session.deviceId);
  expect(errors).toEqual([]);
  await writeFile(`${screenshots}/live-chat-evidence.json`,JSON.stringify({users:Object.values(users).map(u=>u.user_id),roomId:await roomId,eventId,wireType:encrypted.type,devicePreserved:true,reload:true,reconnect:true,duplicateCount:1,isolation:true,groupDelivery:true,accountSwitch:true,pageErrors:errors},null,2));
 }finally{await Promise.all(contexts.map(c=>c.close()))}
});

test('original forms register accounts and persist Capsule pages in the correct account',async({page})=>{
 test.setTimeout(180000);const suffix=randomUUID().slice(0,8),password=randomUUID(),first=`ui_first_${suffix}`,second=`ui_second_${suffix}`,title=`PRIVATE_NOTE_${suffix}`;
 await registerUI(page,first,password);await expect(page.locator('.matrix-inbox-entry')).toContainText('подключено');
 const agent=await page.request.get('/api/capsule-agent/task');expect(agent.ok(),await agent.text()).toBeTruthy();expect(typeof(await agent.json()).configured).toBe('boolean');
 await page.getByTitle('Создать',{exact:true}).click();await page.locator('.creation-options').getByRole('button',{name:/Заметка/}).click();
 await page.getByLabel('Название страницы',{exact:true}).fill(title);await page.getByLabel('Текст блока 1',{exact:true}).fill('Сохранить эту запись только в аккаунте автора.');
 await expect(page.locator('.cu-save-status')).toHaveText('Сохранено',{timeout:15000});await page.getByLabel('Закрыть страницу',{exact:true}).click();
 await page.reload();await page.getByRole('button',{name:'Капсула',exact:true}).click();await page.locator('.cu-section-nav').getByRole('button',{name:'Блокнот',exact:true}).click();await expect(page.locator('.cu-page-list')).toContainText(title);
 await signOut(page);await registerUI(page,second,password);
 await page.getByRole('button',{name:'Капсула',exact:true}).click();await page.locator('.cu-section-nav').getByRole('button',{name:'Блокнот',exact:true}).click();await expect(page.locator('body')).not.toContainText(title);
 await signOut(page);await login(page,first,password);
 await page.getByRole('button',{name:'Капсула',exact:true}).click();await page.locator('.cu-section-nav').getByRole('button',{name:'Блокнот',exact:true}).click();await expect(page.locator('.cu-page-list')).toContainText(title);
});

test('original navigation and responsive screens load without demo posts or broken resources',async({browser})=>{
 test.setTimeout(120000);await mkdir(screenshots,{recursive:true});
 for(const [size,width,height] of [['desktop',1440,1000],['mobile',390,844]] as const){
  const page=await browser.newPage({viewport:{width,height}}),errors:string[]=[],failed:string[]=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('response',r=>{if(r.status()>=400&&!r.url().includes('/api/')&&!r.url().includes('/_matrix/'))failed.push(r.url())});
  await registerUI(page,'visual_'+size+'_'+randomUUID().slice(0,8),randomUUID());await page.getByRole('button',{name:'Главная',exact:true}).click();await expect(page.locator('.social-brandbar')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('Кирилл Воронов');
  await page.screenshot({animations:'disabled',path:`${screenshots}/main-${size}-home.png`});
  await page.screenshot({animations:'disabled',path:`${screenshots}/main-${size}-feed.png`});
  await chats(page);await page.screenshot({animations:'disabled',path:`${screenshots}/main-${size}-chats.png`});
  await page.getByRole('button',{name:'Главная',exact:true}).click();await page.getByRole('tab',{name:'Комиссионка',exact:true}).click();
  await expect(page.locator('.commissionka')).toBeVisible();await page.screenshot({animations:'disabled',path:`${screenshots}/main-${size}-marketplace.png`});
  await page.getByRole('button',{name:'Капсула',exact:true}).click();await expect(page.locator('.cu-universe')).toBeVisible();
  await page.screenshot({animations:'disabled',path:`${screenshots}/main-${size}-capsule.png`});
  await page.getByTitle('Создать',{exact:true}).click();await expect(page.locator('.creation-sheet')).toBeVisible();await page.waitForTimeout(700);
  await page.screenshot({animations:'disabled',path:`${screenshots}/main-${size}-create.png`});
  await page.getByLabel('Закрыть меню создания').click();await page.getByRole('button',{name:'Профиль',exact:true}).click();
  await expect(page.locator('body')).toContainText('Профиль');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);expect(failed).toEqual([]);await page.close();
 }
});
