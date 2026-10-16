import {chromium} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
const root='../.cache/frontend-correction-browser';await mkdir(root,{recursive:true});
const browser=await chromium.launch({headless:true});
for(const [name,port] of [['reference',5180],['main',5173]]){
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
 page.on('pageerror',e=>errors.push(String(e)));
 const response=await page.goto('http://127.0.0.1:'+port,{timeout:180000,waitUntil:'domcontentloaded'});
 await page.waitForTimeout(3000);await page.screenshot({path:root+'/'+name+'-initial.png'});
 console.log(name,response.status(),(await page.locator('body').innerText()).slice(0,2500),errors);await page.close();
}
await browser.close();
