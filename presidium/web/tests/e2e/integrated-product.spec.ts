import { test, expect, type Page } from '@playwright/test'
import { DatabaseSync } from 'node:sqlite'
import { randomUUID } from 'node:crypto'
import { existsSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

test('integrated profiles, social, private channels, Capsule, media and sandbox checkout', async ({browser,request},info)=>{
  test.setTimeout(180_000)
  const hs='http://localhost:8008', suffix=randomUUID().slice(0,8), password=randomUUID()
  const contexts=[], users: {username:string;userId:string;token:string;page:Page}[]=[]
  const database=resolve(process.env.PRESIDIUM_TEST_PRODUCT_DB??'../.data/product/product.sqlite3')
  const moderatorFile=resolve('../.cache/e2e-moderator.json')
  let moderator:{username:string;password:string}
  if(existsSync(moderatorFile)) moderator=JSON.parse(readFileSync(moderatorFile,'utf8'))
  else {
    moderator={username:'integration_moderator68',password:randomUUID()}
    const response=await request.post(`${hs}/_matrix/client/v3/register`,{data:{username:moderator.username,password:moderator.password,auth:{type:'m.login.dummy'}}})
    expect(response.ok(),await response.text()).toBeTruthy()
    const account=await response.json()
    await request.post(`${hs}/_matrix/client/v3/logout`,{headers:{Authorization:`Bearer ${account.access_token}`},data:{}})
    mkdirSync(resolve('../.cache'),{recursive:true});writeFileSync(moderatorFile,JSON.stringify(moderator),{mode:0o600})
  }
  const moderatorLogin=await request.post(`${hs}/_matrix/client/v3/login`,{data:{type:'m.login.password',identifier:{type:'m.id.user',user:moderator.username},password:moderator.password}})
  expect(moderatorLogin.ok()).toBeTruthy()
  const moderatorSession=await moderatorLogin.json(), moderatorHeaders={Authorization:`Bearer ${moderatorSession.access_token}`}
  const api=async(token:string,path:string,body?:unknown)=>{
    const response=body===undefined?await request.get(`/api/${path}`,{headers:{Authorization:`Bearer ${token}`}}):await request.post(`/api/${path}`,{headers:{Authorization:`Bearer ${token}`},data:body})
    expect(response.ok(),await response.text()).toBeTruthy();return response.json()
  }
  const nav=async(page:Page,name:string)=>{await page.getByRole('navigation',{name:'Разделы Presidium'}).getByRole('button',{name,exact:true}).click()}
  try {
    for(const role of ['owner','reader']) {
      const context=await browser.newContext();contexts.push(context)
      const page=await context.newPage(),username=`integrated_${role}_${suffix}`
      await page.goto('/')
      await page.getByLabel('Username',{exact:true}).fill(username);await page.getByLabel('Password',{exact:true}).fill(password)
      await page.getByRole('button',{name:'Создать аккаунт',exact:true}).click()
      await expect(page.getByTestId('authenticated-state')).toBeVisible({timeout:45000})
      const session=await page.evaluate(()=>{const key=Object.keys(localStorage).find(k=>k.startsWith('presidium_matrix_session_'))!;return JSON.parse(localStorage.getItem(key)!)})
      users.push({username,userId:session.userId,token:session.accessToken,page})
      await nav(page,'Профиль');await expect(page.getByLabel('Имя профиля')).toBeVisible()
      const db=new DatabaseSync(database),now=Date.now()
      db.prepare(`INSERT INTO subscriptions(user_id,tier,status,period_start,period_end,updated_at,last_event_id) VALUES(?,'business','active',?,?,?,'browser-test-fixture') ON CONFLICT(user_id) DO UPDATE SET tier='business',period_end=excluded.period_end`).run(session.userId,now-1000,now+86400000,now)
      db.close()
    }
    const [owner,reader]=users
    await owner.page.getByLabel('Имя профиля').fill(`Owner ${suffix}`)
    await owner.page.getByRole('button',{name:'Сохранить профиль',exact:true}).click()
    await expect.poll(async()=>(await api(owner.token,'account')).profile.name).toBe(`Owner ${suffix}`)
    await nav(owner.page,'Лента')
    const publication=`Live product post ${suffix}`
    await owner.page.getByLabel('Текст публикации').fill(publication)
    await owner.page.getByRole('button',{name:'Опубликовать',exact:true}).click()
    await expect(owner.page.locator('.product-card').filter({hasText:publication})).toBeVisible()
    await nav(reader.page,'Лента')
    const received=reader.page.locator('.product-card').filter({hasText:publication})
    await expect(received).toBeVisible()
    await received.getByRole('button',{name:'Комментарии',exact:true}).click()
    await received.getByLabel('Текст комментария').fill(`Comment ${suffix}`)
    await received.getByRole('button',{name:'Добавить комментарий',exact:true}).click()
    await expect(received).toContainText(`Comment ${suffix}`)
    await received.getByRole('button',{name:'Подписаться на автора',exact:true}).click()
    await expect(received.getByRole('button',{name:'Отписаться',exact:true})).toBeVisible()
    await owner.page.getByLabel('Текст публикации').fill(`Story ${suffix}`)
    await owner.page.getByRole('button',{name:'Stories на 24 часа',exact:true}).click()
    await expect(owner.page.locator('.product-stories')).toContainText(`Story ${suffix}`)
    await nav(owner.page,'Пространства')
    await owner.page.getByLabel('Название пространства').fill(`Channel ${suffix}`)
    await owner.page.getByLabel('Вид пространства').selectOption('channel')
    await owner.page.getByRole('button',{name:'Создать пространство',exact:true}).click()
    await owner.page.getByRole('button',{name:'Создать приглашение на 24 часа',exact:true}).click()
    const invite=await owner.page.getByLabel('Приглашение',{exact:true}).inputValue()
    await nav(reader.page,'Пространства')
    await reader.page.getByLabel('Код приглашения').fill(invite)
    await reader.page.getByRole('button',{name:'Вступить',exact:true}).click()
    await expect(reader.page.locator('.product-card').filter({hasText:`Channel ${suffix}`})).toBeVisible()
    await owner.page.getByLabel('Название группового чата').fill(`Encrypted group ${suffix}`)
    await owner.page.getByLabel('Matrix ID участников через запятую').fill(reader.userId)
    await owner.page.getByRole('button',{name:'Создать зашифрованный чат',exact:true}).click()
    await reader.page.getByRole('button',{name:`Принять приглашение: Encrypted group ${suffix}`,exact:true}).click({timeout:30000})
    await owner.page.getByLabel('Сообщение группе',{exact:true}).fill(`Group secret ${suffix}`)
    await owner.page.getByRole('button',{name:'Отправить в группу',exact:true}).click()
    await expect(reader.page.getByLabel('Сообщения группы')).toContainText(`Group secret ${suffix}`,{timeout:30000})
    const group=await owner.page.locator('[data-group-room]').getAttribute('data-group-room')
    const eventId=await reader.page.getByLabel('Сообщения группы').locator('[data-event-id]').filter({hasText:`Group secret ${suffix}`}).getAttribute('data-event-id')
    const raw=await request.get(`${hs}/_matrix/client/v3/rooms/${encodeURIComponent(group!)}/event/${encodeURIComponent(eventId!)}`,{headers:{Authorization:`Bearer ${owner.token}`}})
    expect(raw.ok()).toBeTruthy();const encrypted=await raw.json();expect(encrypted.type).toBe('m.room.encrypted');expect(JSON.stringify(encrypted)).not.toContain(`Group secret ${suffix}`)
    await nav(owner.page,'Capsule')
    await owner.page.getByLabel('Название записи').fill(`Private Capsule ${suffix}`)
    await owner.page.getByLabel('Текст записи').fill('Private persistent text')
    await owner.page.getByRole('button',{name:'Сохранить запись',exact:true}).click()
    await expect(owner.page.locator('.product-card').filter({hasText:`Private Capsule ${suffix}`})).toBeVisible()
    await owner.page.reload();await expect(owner.page.getByTestId('authenticated-state')).toBeVisible({timeout:45000});await nav(owner.page,'Capsule')
    await expect(owner.page.locator('.product-card').filter({hasText:`Private Capsule ${suffix}`})).toBeVisible()
    await nav(reader.page,'Capsule');await expect(reader.page.locator('.product-card')).toHaveCount(0)
    await nav(owner.page,'Комиссионка')
    await owner.page.getByLabel('Название товара').fill(`Item ${suffix}`)
    await owner.page.getByLabel('Описание товара').fill('A genuine used item description for integration testing')
    await owner.page.getByLabel('Город',{exact:true}).fill('Москва');await owner.page.getByLabel('Цена, рубли').fill('10')
    await owner.page.getByLabel('Фото товара').setInputFiles(resolve('public/editorial/editorial-city.png'))
    await owner.page.getByRole('button',{name:'Отправить на модерацию',exact:true}).click()
    await expect(owner.page.locator('.product-card').filter({hasText:`Item ${suffix}`})).toBeVisible({timeout:30000})
    const own=await api(owner.token,'marketplace?view=own'),listing=own.listings[0]
    // Real authenticated moderator operation; the test never bypasses backend authorization.
    await api(moderatorSession.access_token,'account')
    await api(moderatorSession.access_token,'marketplace',{action:'moderateListing',listingId:listing.id,revision:listing.revision,decision:'approve',conditionVerified:true,rightToSellVerified:true})
    await nav(reader.page,'Комиссионка')
    const item=reader.page.locator('.product-card').filter({hasText:`Item ${suffix}`})
    await item.getByRole('button',{name:'Оформить тестовый заказ',exact:true}).click()
    await reader.page.getByRole('button',{name:'Тестовая оплата без списания',exact:true}).click()
    await expect(reader.page.locator('.product-card').filter({hasText:'paid_sandbox'})).toBeVisible()
    const orders=await api(reader.token,'marketplace?view=orders')
    expect(orders.orders[0].financials.buyerTotalKopecks).toBe(1000)
    await reader.page.screenshot({path:info.outputPath('integrated-order.png'),fullPage:true})
    console.log('Live product: registration, profile, cross-account post/comment/follow, Story, channel invitation, persistent private Capsule, photo, sandbox checkout/payment passed')
  } finally {
    for(const u of users) {
      // Remove public test content through its real owner API; keep sandbox order audit rows.
      const feed=await api(u.token,'social').catch(()=>null)
      if(feed) {
        for(const p of feed.posts.filter((p:{authorId:string})=>p.authorId===u.userId))await api(u.token,'social',{action:'deletePost',id:p.id}).catch(()=>null)
        for(const s of feed.stories.filter((s:{authorId:string})=>s.authorId===u.userId))await api(u.token,'social',{action:'deleteStory',id:s.id}).catch(()=>null)
      }
      await request.post(`${hs}/_matrix/client/v3/account/deactivate`,{headers:{Authorization:`Bearer ${u.token}`},data:{auth:{type:'m.login.password',identifier:{type:'m.id.user',user:u.userId},password},erase:true}})
    }
    await request.post(`${hs}/_matrix/client/v3/logout`,{headers:moderatorHeaders,data:{}})
    await Promise.all(contexts.map(c=>c.close()))
  }
})
