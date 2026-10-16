import {chromium} from '@playwright/test';
import {readFile,writeFile,access} from 'node:fs/promises';
const root='../.cache/frontend-correction-browser';
const browser=await chromium.launch({headless:true});
try{
 for(const [size,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
  const page=await browser.newPage({viewport:{width,height}}),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto('http://127.0.0.1:5173');await page.getByRole('button',{name:'Капсула',exact:true}).click();
  await page.getByLabel('Настройки пользователя',{exact:true}).click();await page.getByRole('heading',{name:'Настройки',exact:true}).waitFor();
  await page.screenshot({path:`${root}/main-${size}-settings.png`,animations:'disabled'});
  await page.getByRole('button',{name:'Профиль',exact:true}).click();await page.getByRole('dialog').waitFor();
  await page.screenshot({path:`${root}/main-${size}-profile.png`,animations:'disabled'});
  if(errors.length)throw Error(errors.join('\n'));await page.close();
 }
}finally{await browser.close()}
const playwright=JSON.parse(await readFile(`${root}/playwright-production.json`,'utf8'));
if(playwright.stats.unexpected||playwright.stats.expected!==3)throw Error('Production browser acceptance incomplete');
const styles=JSON.parse(await readFile(`${root}/assets-styles-verification.json`,'utf8'));
if(Object.values(styles).some(x=>!x.identical))throw Error('Original resources differ');
const screens=[['home','Главный экран'],['chats','Список чатов'],['conversation','Переписка'],['feed','Лента'],['marketplace','Комиссионка'],['capsule','Capsule'],['create','Меню создания']];
let content='';
for(const [size,label] of [['desktop','1440 × 1000'],['mobile','390 × 844']]){
 content+=`<h2>${label}</h2>`;
 for(const [name,title] of screens){
  for(const source of ['reference','main'])await access(`${root}/${source}-${size}-${name}.png`);
  content+=`<section><h3>${title}</h3><div class="pair"><figure><figcaption>Оригинал Argon</figcaption><a href="reference-${size}-${name}.png"><img loading="lazy" src="reference-${size}-${name}.png"></a></figure><figure><figcaption>Объединённый Presidium</figcaption><a href="main-${size}-${name}.png"><img loading="lazy" src="main-${size}-${name}.png"></a></figure></div></section>`;
 }
}
const html=`<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Presidium — сравнение оригинального frontend</title><style>body{margin:24px;background:#0c1118;color:#e7edf5;font:16px system-ui}h1{font-size:26px}p{max-width:1000px;line-height:1.6}a{color:#8bcfff}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0}figcaption{margin:8px 0;color:#9daebb}img{display:block;width:100%;height:auto;border:1px solid #334151}section{margin:36px 0}section:has(img[src*="mobile"]){max-width:820px}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style><h1>Оригинальный интерфейс и объединённый продукт</h1><p>Одинаковые размеры окна. В эталоне сохранены исходные примеры; в основном приложении показаны реальные тестовые Matrix-диалоги или пустой аккаунт. Все 34 файла CSS и ресурсов совпадают с проверенным исходником. Подписи авторизации, подтверждения сервера и доступности функций отражают подключённые операции. Положение уведомлений изменено, чтобы они не перекрывали отправку.</p><p>Production: 3 браузерных сценария прошли. <a href="live-chat-evidence.json">Сетевая доставка, E2EE и сохранность устройства</a> · <a href="playwright-production.json">Полный браузерный результат</a> · <a href="assets-styles-verification.json">Сравнение оригинальных файлов</a></p>${content}</html>`;
await writeFile(`${root}/comparison.html`,html);
console.log('Verified: settings/profile at both widths, 28 comparison screenshots, 34 original CSS/assets, 3 production browser tests.');
