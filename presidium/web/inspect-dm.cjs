const { chromium } = require('playwright');
const { execSync } = require('child_process');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const aliceContext = await browser.newContext();
  const bobContext = await browser.newContext();
  const alicePage = await aliceContext.newPage();
  const bobPage = await bobContext.newPage();

  async function loginAndCapture(page, user, passw, recipient) {
    await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Username').fill(user);
    await page.getByLabel('Password').fill(passw);
    await page.getByLabel('Recipient Matrix user ID').fill(recipient);
    await page.getByRole('button', { name: /Login & initialize crypto/i }).click();
    await page.waitForTimeout(12000);
    const room = await page.locator('.room-id').textContent();
    const session = await page.evaluate(() => Object.fromEntries([...Object.keys(localStorage).map((k) => [k, localStorage.getItem(k)])]));
    return { room, session };
  }

  const alice = await loginAndCapture(alicePage, 'alice', 'alice-password', '@bob:presidium.local');
  const bob = await loginAndCapture(bobPage, 'bob', 'bob-password', '@alice:presidium.local');

  for (const [label, data] of [['alice', alice], ['bob', bob]]) {
    const parsed = JSON.parse(data.session['presidium_matrix_session_' + label]);
    const url = `http://localhost:8008/_matrix/client/v3/sync?timeout=3000`;
    const response = execSync(`curl -s -H "Authorization: Bearer ${parsed.accessToken}" "${url}"`, { encoding: 'utf8' });
    console.log('=== ' + label.toUpperCase() + ' SYNC ===');
    console.log(response.slice(0, 4000));
  }

  console.log('ALICE_ROOM', alice.room);
  console.log('BOB_ROOM', bob.room);

  await aliceContext.close();
  await bobContext.close();
  await browser.close();
})();
