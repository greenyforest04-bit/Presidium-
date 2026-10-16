import { randomUUID } from 'node:crypto'
import { test, expect, type Page } from '@playwright/test'

const hs = 'http://localhost:8008'

test('encrypted delivery, reload, room isolation, account isolation and plaintext refusal', async ({ browser, request }) => {
  test.setTimeout(180_000)
  const suffix = randomUUID().replaceAll('-', '').slice(0, 10)
  const password = randomUUID()
  const users: Record<string, { username: string; user_id: string; access_token: string }> = {}
  const contexts = []
  const pages: Record<string, Page> = {}
  for (const name of ['alice', 'bob', 'carol']) {
    const username = `test_${name}_${suffix}`
    const response = await request.post(`${hs}/_matrix/client/v3/register`, { data: { username, password, auth: { type: 'm.login.dummy' } } })
    expect(response.ok(), await response.text()).toBeTruthy()
    users[name] = { username, ...await response.json() }
    const profile = await request.put(`${hs}/_matrix/client/v3/profile/${encodeURIComponent(users[name].user_id)}/displayname`, {
      headers: { Authorization: `Bearer ${users[name].access_token}` }, data: { displayname: username },
    })
    expect(profile.ok()).toBeTruthy()
    const context = await browser.newContext()
    contexts.push(context)
    const page = await context.newPage()
    pages[name] = page
    await page.goto('/')
    await page.getByLabel('Username', { exact: true }).fill(username)
    await page.getByLabel('Password', { exact: true }).fill(password)
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
    await expect(page.getByTestId('authenticated-state')).toBeVisible({ timeout: 45_000 })
    console.log(`Signed in ${name}`)
  }
  const { alice, bob, carol } = pages
  async function openChat(page: Page, name: string) {
    await page.getByLabel('Search people').fill(users[name].user_id)
    await page.getByRole('button', { name: 'Search', exact: true }).click()
    await page.getByLabel('People search results').getByRole('button', { name: users[name].username, exact: true }).click({ timeout: 15_000 })
    await page.getByRole('button', { name: 'Open chat', exact: true }).click()
    await expect(page.getByText('Encrypted', { exact: true })).toBeVisible({ timeout: 30_000 })
  }
  async function send(page: Page, text: string) {
    await page.getByLabel('Message', { exact: true }).fill(text)
    await page.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(page.locator('.message-list')).toContainText(text, { timeout: 30_000 })
  }
  try {
    await openChat(alice, 'bob')
    const ab = `AB_PRIVATE_${suffix}`
    await send(alice, ab)
    console.log('Sent encrypted Alice/Bob message')
    await expect(bob.locator('.message-list')).toContainText(ab, { timeout: 30_000 })
    const message = alice.locator('.message-list > div').filter({ hasText: ab })
    const room = await message.getAttribute('data-room-id'), event = await message.getAttribute('data-event-id')
    const session = await alice.evaluate(() => {
      const key = Object.keys(localStorage).find((k) => k.startsWith('presidium_matrix_session_'))!
      return JSON.parse(localStorage.getItem(key)!)
    })
    const rawResponse = await request.get(`${hs}/_matrix/client/v3/rooms/${encodeURIComponent(room!)}/event/${encodeURIComponent(event!)}`, { headers: { Authorization: `Bearer ${session.accessToken}` } })
    expect(rawResponse.ok()).toBeTruthy()
    const raw = await rawResponse.json()
    expect(raw.type).toBe('m.room.encrypted')
    expect(raw.content.ciphertext).toBeTruthy()
    expect(JSON.stringify(raw)).not.toContain(ab)
    await Promise.all([alice.reload(), bob.reload()])
    await expect(alice.locator('.message-list')).toContainText(ab, { timeout: 45_000 })
    await expect(bob.locator('.message-list')).toContainText(ab, { timeout: 45_000 })
    console.log('Reloaded history')
    await openChat(alice, 'carol')
    const ac = `AC_PRIVATE_${suffix}`
    await send(alice, ac)
    await expect(carol.locator('.message-list')).toContainText(ac, { timeout: 30_000 })
    await expect(alice.locator('.message-list')).not.toContainText(ab)
    await send(bob, `BOB_BACKGROUND_${suffix}`)
    await expect(alice.locator('.message-list')).not.toContainText(`BOB_BACKGROUND_${suffix}`)
    await send(alice, `STILL_CAROL_${suffix}`)
    await expect(carol.locator('.message-list')).toContainText(`STILL_CAROL_${suffix}`)
    await expect(bob.locator('.message-list')).not.toContainText(ac)

    // An unencrypted room with the same person must not be reused as a DM.
    const plainResponse = await request.post(`${hs}/_matrix/client/v3/createRoom`, { headers: { Authorization: `Bearer ${users.alice.access_token}` }, data: { invite: [users.bob.user_id], is_direct: true, preset: 'trusted_private_chat' } })
    expect(plainResponse.ok()).toBeTruthy()
    const plain = await plainResponse.json()
    await openChat(alice, 'bob')
    await expect(alice.getByTestId('conversation')).not.toHaveAttribute('data-room-id', plain.room_id)
    await expect(alice.locator('.message-list')).toContainText(ab)
    await expect(alice.locator('.message-list')).not.toContainText(ac)
    console.log('Verified conversation isolation and plaintext-room refusal')

    // Switching accounts clears both the old room and the old message view.
    await alice.getByLabel('Username', { exact: true }).fill(users.carol.username)
    await alice.getByLabel('Password', { exact: true }).fill(password)
    await alice.getByRole('button', { name: 'Sign in', exact: true }).click()
    await expect(alice.getByTestId('authenticated-state')).toContainText(users.carol.username.slice(1), { timeout: 45_000 })
    await expect(alice.locator('.message-list')).not.toContainText(ab)
    await alice.getByRole('button', { name: 'Sign out', exact: true }).click()
    await expect(alice.getByTestId('authenticated-state')).toHaveCount(0)
    await expect(alice.getByTestId('conversation')).toHaveAttribute('data-room-id', '')
  } finally {
    await Promise.all(contexts.map((context) => context.close()))
    for (const user of Object.values(users)) {
      await request.post(`${hs}/_matrix/client/v3/account/deactivate`, { headers: { Authorization: `Bearer ${user.access_token}` }, data: { auth: { type: 'm.login.password', identifier: { type: 'm.id.user', user: user.user_id }, password }, erase: true } })
    }
  }
})
