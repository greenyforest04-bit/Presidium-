const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const aliceContext = await browser.newContext();
  const bobContext = await browser.newContext();
  const alicePage = await aliceContext.newPage();
  const bobPage = await bobContext.newPage();

  for (const [page, label] of [[alicePage, 'alice'], [bobPage, 'bob']]) {
    page.on('console', (message) => {
      const text = message.text();
      if (/Encrypting for users|Starting encryption|shareRoomKey|decrypt|createDirectConversation|Error|WARN/.test(text)) {
        console.log(`[${label}] ${message.type()} ${text.slice(0, 1400)}`);
      }
    });
    page.on('pageerror', (error) => console.log(`[${label}] PAGEERROR ${error.message}`));
  }

  async function login(page, username, password, recipient) {
    await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Username').fill(username);
    await page.getByLabel('Password').fill(password);
    await page.getByLabel('Recipient Matrix user ID').fill(recipient);
    await page.getByRole('button', { name: /Login & initialize crypto/i }).click();
    await page.locator('.room-id').waitFor({ timeout: 60_000 });
    await page.waitForTimeout(5_000);
    return await page.locator('.room-id').innerText();
  }

  const aliceRoom = await login(alicePage, 'alice', 'alice-password', '@bob:presidium.local');
  const bobRoom = await login(bobPage, 'bob', 'bob-password', '@alice:presidium.local');
  console.log('ROOMS', { aliceRoom, bobRoom });

  const secret = `PRESIDIUM_DIAG_${Date.now()}`;
  await alicePage.getByLabel('Message').fill(secret);
  await alicePage.getByRole('button', { name: /Send encrypted message/i }).click();
  await bobPage.locator('.message-list').getByText(secret).waitFor({ timeout: 35_000 }).catch(() => {});
  await alicePage.waitForTimeout(2_000);

  console.log('ALICE_LIST', await alicePage.locator('.message-list').innerText());
  console.log('BOB_LIST', await bobPage.locator('.message-list').innerText());

  await aliceContext.close();
  await bobContext.close();
  await browser.close();
})();
