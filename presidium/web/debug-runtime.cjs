const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const aliceContext = await browser.newContext();
  const bobContext = await browser.newContext();
  const alicePage = await aliceContext.newPage();
  const bobPage = await bobContext.newPage();

  const attach = (page, label) => {
    page.on('console', (msg) => {
      console.log(`[${label}] CONSOLE ${msg.type()}: ${msg.text()}`);
    });
    page.on('pageerror', (err) => {
      console.log(`[${label}] PAGEERROR: ${err.message}`);
    });
  };

  attach(alicePage, 'alice');
  attach(bobPage, 'bob');

  async function login(page, user, passw, recipient) {
    await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Username').fill(user);
    await page.getByLabel('Password').fill(passw);
    await page.getByLabel('Recipient Matrix user ID').fill(recipient);
    await page.getByRole('button', { name: /Login & initialize crypto/i }).click();
    await page.waitForTimeout(15000);
    console.log(`[${user}] ROOM`, await page.locator('.room-id').textContent());
  }

  await login(alicePage, 'alice', 'alice-password', '@bob:presidium.local');
  await login(bobPage, 'bob', 'bob-password', '@alice:presidium.local');

  await aliceContext.close();
  await bobContext.close();
  await browser.close();
})();
