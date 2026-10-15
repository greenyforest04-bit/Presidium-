const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const aliceContext = await browser.newContext();
  const bobContext = await browser.newContext();
  const alicePage = await aliceContext.newPage();
  const bobPage = await bobContext.newPage();

  async function login(page, user, passw, recipient) {
    await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Username').fill(user);
    await page.getByLabel('Password').fill(passw);
    await page.getByLabel('Recipient Matrix user ID').fill(recipient);
    await page.getByRole('button', { name: /Login & initialize crypto/i }).click();
    await page.waitForTimeout(7000);
  }

  await login(alicePage, 'alice', 'alice-password', '@bob:presidium.local');
  await login(bobPage, 'bob', 'bob-password', '@alice:presidium.local');
  const aliceRoom = await alicePage.locator('.room-id').textContent();
  const bobRoom = await bobPage.locator('.room-id').textContent();
  console.log('ALICE_ROOM', aliceRoom);
  console.log('BOB_ROOM', bobRoom);

  await alicePage.getByLabel('Message').fill('PRESIDIUM_REAL_BROWSER_E2EE_739184');
  await alicePage.getByRole('button', { name: /Send encrypted message/i }).click();
  await alicePage.waitForTimeout(7000);

  const aliceList = await alicePage.locator('.message-list').innerText();
  const bobList = await bobPage.locator('.message-list').innerText();
  console.log('ALICE_LIST', aliceList);
  console.log('BOB_LIST', bobList);

  await aliceContext.close();
  await bobContext.close();
  await browser.close();
})();
