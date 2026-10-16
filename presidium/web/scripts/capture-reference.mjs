import {chromium} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
const root='../.cache/frontend-correction-browser';await mkdir(root,{recursive:true});
const browser=await chromium.launch({headless:true}),report=[];
try{
 for(const [size,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
  const page=await browser.newPage({viewport:{width,height}}),errors=[],failed=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('response',r=>{if(r.status()>=400&&!r.url().includes('/api/'))failed.push(r.url())});
  await page.goto('http://127.0.0.1:5180',{timeout:180000});await page.getByRole('tab',{name:'Для вас',exact:true}).waitFor();
  await page.waitForTimeout(1200);
  const capture=async name=>{await page.screenshot({animations:'disabled',path:`${root}/reference-${size}-${name}.png`});report.push({size,screen:name,geometry:await page.locator('.rail,.journal-width,.inbox,.conversation,.commissionka,.cu-universe,.creation-sheet').evaluateAll(xs=>xs.map(x=>{const b=x.getBoundingClientRect(),s=getComputedStyle(x);return {class:x.className,x:b.x,y:b.y,width:b.width,height:b.height,font:s.fontFamily,color:s.color,background:s.backgroundColor}}))})};
  await capture('home');await capture('feed');await page.getByRole('button',{name:'Сообщения',exact:true}).click();await capture('chats');
  if(size==='mobile')await page.locator('.chat-row').first().click();await capture('conversation');
  if(size==='mobile')await page.getByTitle('К списку чатов',{exact:true}).click();
  await page.getByRole('button',{name:'Главная',exact:true}).click();await page.getByRole('tab',{name:'Комиссионка',exact:true}).click();
  await page.locator('.commissionka').waitFor();await capture('marketplace');
  await page.getByRole('button',{name:'Капсула',exact:true}).click();await page.locator('.cu-universe').waitFor();await capture('capsule');
  await page.getByTitle('Создать',{exact:true}).click();await page.locator('.creation-sheet').waitFor();await page.waitForTimeout(700);await capture('create');
  report.push({size,errors,failed});await page.close();
 }
 await writeFile(`${root}/reference-screen-report.json`,JSON.stringify(report,null,2));console.log('Original reference: 14 screenshots captured',report.filter(x=>x.errors));
}finally{await browser.close()}
