import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { expect, test, type BrowserContext, type Page } from '@playwright/test'

const artifactDir = path.resolve('artifacts')
const homeserverUrl = 'http://localhost:8008'
const aliceSecret = 'PRESIDIUM_REAL_BROWSER_E2EE_739184'
const bobReply = 'PRESIDIUM_REAL_BROWSER_REPLY_48251'
const postRestartMessage = 'PRESIDIUM_REAL_BROWSER_POST_RESTART_991'

test.setTimeout(180_000)

interface RuntimeCapture {
  consoleErrors: string[]
  pageErrors: string[]
  failedRequests: string[]
}

function captureRuntime(page: Page, label: string): RuntimeCapture {
  const capture: RuntimeCapture = {
    consoleErrors: [],
    pageErrors: [],
    failedRequests: [],
  }

  page.on('console', (message) => {
    if (message.type() === 'error') {
      capture.consoleErrors.push(`[${label}] ${message.text()}`)
    }
  })
  page.on('pageerror', (error) => capture.pageErrors.push(`[${label}] ${error.message}`))
  page.on('requestfailed', (request) => {
    capture.failedRequests.push(
      `[${label}] ${request.url()} :: ${request.failure()?.errorText ?? 'unknown error'}`,
    )
  })

  return capture
}

async function signIn(page: Page, username: string, password: string) {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('.app-shell')).toBeVisible()
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('authenticated-state')).toHaveText(
    `${username[0].toUpperCase()}${username.slice(1)} logged in`,
    { timeout: 45_000 },
  )
}

async function waitForHomeserver() {
  const deadline = Date.now() + 45_000

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${homeserverUrl}/_matrix/client/versions`)
      if (response.ok) {
        return
      }
    } catch {
      // Tuwunel can refuse connections while its container is restarting.
    }

    await new Promise((resolve) => setTimeout(resolve, 500))
  }

  throw new Error('Tuwunel did not become healthy after restart.')
}

async function getSession(page: Page, username: string) {
  return page.evaluate((user) => {
    const key = `presidium_matrix_session_${user}`
    const value = window.localStorage.getItem(key)
    if (!value) {
      throw new Error(`Missing persisted session for ${user}`)
    }
    return JSON.parse(value) as { userId: string; deviceId: string; accessToken: string }
  }, username)
}

async function verifyEncryptedEvent(
  context: BrowserContext,
  session: Awaited<ReturnType<typeof getSession>>,
  roomId: string,
  eventId: string,
  plaintext: string,
) {
  expect(eventId, 'The UI must expose the final server event ID after sending.').toMatch(/^\$/)
  const response = await context.request.get(
    `${homeserverUrl}/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/event/${encodeURIComponent(eventId)}`,
    { headers: { Authorization: `Bearer ${session.accessToken}` } },
  )
  expect(
    response.ok(),
    `Raw event request returned ${response.status()} for ${roomId}/${eventId}: ${await response.text()}`,
  ).toBeTruthy()

  const rawEvent = await response.json()
  const serverVisiblePayload = JSON.stringify(rawEvent)
  expect(rawEvent.type).toBe('m.room.encrypted')
  expect(serverVisiblePayload).not.toContain(plaintext)
  expect(rawEvent.content).toHaveProperty('ciphertext')
}

test('real Presidium UI proves Alice/Bob client-side E2EE', async ({ browser }) => {
  mkdirSync(artifactDir, { recursive: true })

  const aliceContext = await browser.newContext()
  const bobContext = await browser.newContext()
  const alicePage = await aliceContext.newPage()
  const bobPage = await bobContext.newPage()
  const aliceRuntime = captureRuntime(alicePage, 'alice')
  const bobRuntime = captureRuntime(bobPage, 'bob')

  await aliceContext.tracing.start({ screenshots: true, snapshots: true, sources: true })
  await bobContext.tracing.start({ screenshots: true, snapshots: true, sources: true })

  try {
    await signIn(alicePage, 'alice', 'alice-password')
    await alicePage.screenshot({ path: path.join(artifactDir, '01-alice-logged-in.png'), fullPage: true })

    await signIn(bobPage, 'bob', 'bob-password')
    await bobPage.screenshot({ path: path.join(artifactDir, '02-bob-logged-in.png'), fullPage: true })

    await alicePage.getByLabel('Search people').fill('bob')
    await alicePage.getByRole('button', { name: 'Search', exact: true }).click()
    const bobResult = alicePage.getByLabel('People search results').getByRole('button', { name: /bob/i })
    await expect(bobResult).toBeVisible({ timeout: 30_000 })
    await bobResult.click()
    await alicePage.getByRole('button', { name: 'Open chat' }).click()
    await expect(alicePage.getByTestId('conversation')).toHaveAttribute('data-room-id', /!/, {
      timeout: 45_000,
    })
    await alicePage.screenshot({ path: path.join(artifactDir, '03-alice-opens-bob-chat.png'), fullPage: true })
    await expect(alicePage.getByText('Encrypted', { exact: true })).toBeVisible()
    await alicePage.screenshot({ path: path.join(artifactDir, '04-encrypted-chat.png'), fullPage: true })

    const roomId = await alicePage.getByTestId('conversation').getAttribute('data-room-id')
    expect(roomId).toBeTruthy()

    await alicePage.getByLabel('Message').fill(aliceSecret)
    await alicePage.getByRole('button', { name: 'Send', exact: true }).click()
    const aliceMessage = alicePage.locator('.message-list > div').filter({ hasText: aliceSecret })
    await expect(aliceMessage).toBeVisible({ timeout: 30_000 })
    await alicePage.screenshot({ path: path.join(artifactDir, '05-alice-sent-message.png'), fullPage: true })

    const bobMessage = bobPage.locator('.message-list > div').filter({ hasText: aliceSecret })
    await expect(bobMessage).toBeVisible({ timeout: 35_000 })
    await bobPage.screenshot({ path: path.join(artifactDir, '06-bob-received-message.png'), fullPage: true })

    await bobPage.getByLabel('Message').fill(bobReply)
    await bobPage.getByRole('button', { name: 'Send', exact: true }).click()
    const aliceReply = alicePage.locator('.message-list > div').filter({ hasText: bobReply })
    await expect(aliceReply).toBeVisible({ timeout: 35_000 })
    await alicePage.screenshot({ path: path.join(artifactDir, '07-alice-received-reply.png'), fullPage: true })

    const aliceRoomId = await aliceMessage.getAttribute('data-room-id')
    const aliceEventId = await aliceMessage.getAttribute('data-event-id')
    const bobEventId = await aliceReply.getAttribute('data-event-id')
    expect(aliceRoomId).toBe(roomId)
    expect(aliceEventId).toBeTruthy()
    expect(bobEventId).toBeTruthy()

    const aliceSession = await getSession(alicePage, 'alice')
    const bobSession = await getSession(bobPage, 'bob')
    await verifyEncryptedEvent(aliceContext, aliceSession, roomId!, aliceEventId!, aliceSecret)
    await verifyEncryptedEvent(aliceContext, aliceSession, roomId!, bobEventId!, bobReply)

    await alicePage.reload({ waitUntil: 'domcontentloaded' })
    await bobPage.reload({ waitUntil: 'domcontentloaded' })
    await expect(alicePage.getByTestId('authenticated-state')).toHaveText('Alice logged in', { timeout: 45_000 })
    await expect(bobPage.getByTestId('authenticated-state')).toHaveText('Bob logged in', { timeout: 45_000 })
    await expect(alicePage.getByTestId('conversation')).toHaveAttribute('data-room-id', roomId!)
    await expect(bobPage.getByTestId('conversation')).toHaveAttribute('data-room-id', roomId!)
    await expect(alicePage.locator('.message-list')).toContainText(aliceSecret)
    await expect(alicePage.locator('.message-list')).toContainText(bobReply)
    await expect(bobPage.locator('.message-list')).toContainText(aliceSecret)
    await expect(bobPage.locator('.message-list')).toContainText(bobReply)
    await expect(alicePage.locator('.message-list')).not.toContainText('Unable to decrypt')
    await expect(bobPage.locator('.message-list')).not.toContainText('Unable to decrypt')
    await alicePage.screenshot({ path: path.join(artifactDir, '08-alice-after-reload.png'), fullPage: true })
    await bobPage.screenshot({ path: path.join(artifactDir, '09-bob-after-reload.png'), fullPage: true })

    execFileSync('docker', ['compose', 'restart', 'tuwunel'], {
      cwd: path.resolve(process.cwd(), '..'),
      stdio: 'pipe',
      timeout: 60_000,
    })
    await waitForHomeserver()

    const postRestartSync = alicePage.waitForResponse(
      (response) => response.url().includes('/_matrix/client/v3/sync') && response.ok(),
      { timeout: 45_000 },
    )
    await postRestartSync
    await alicePage.getByLabel('Message').fill(postRestartMessage)
    await alicePage.getByRole('button', { name: 'Send', exact: true }).click()
    await expect(alicePage.locator('.message-list')).toContainText(postRestartMessage, { timeout: 30_000 })
    await expect(bobPage.locator('.message-list')).toContainText(postRestartMessage, { timeout: 35_000 })
    await alicePage.screenshot({ path: path.join(artifactDir, '10-after-tuwunel-restart.png'), fullPage: true })

    const runtime = [aliceRuntime, bobRuntime]
    const consoleErrors = runtime.flatMap((capture) => capture.consoleErrors)
    const pageErrors = runtime.flatMap((capture) => capture.pageErrors)
    const failedRequests = runtime.flatMap((capture) => capture.failedRequests)

    console.log('E2E_ROOM_ID', roomId)
    console.log('E2E_ALICE_USER_ID', aliceSession.userId)
    console.log('E2E_BOB_USER_ID', bobSession.userId)
    console.log('E2E_ALICE_DEVICE_ID', aliceSession.deviceId)
    console.log('E2E_BOB_DEVICE_ID', bobSession.deviceId)
    console.log('E2E_SERVER_EVENT_TYPES', 'm.room.encrypted, m.room.encrypted')
    console.log('E2E_PLAINTEXT_ABSENT_ON_SERVER', true)
    console.log('E2E_RELOAD_HISTORY', 'passed')
    console.log('E2E_TUWUNEL_RESTART', 'passed')
    console.log('E2E_BROWSER_CONSOLE_ERRORS', consoleErrors.length)
    console.log('E2E_CONSOLE_ERROR_DETAILS', consoleErrors)
    console.log('E2E_PAGE_ERRORS', pageErrors)
    console.log('E2E_FAILED_REQUESTS', failedRequests)

    expect(pageErrors).toEqual([])
    expect(consoleErrors.filter((error) => !error.includes('/voip/turnServer'))).toEqual([])
  } finally {
    await aliceContext.tracing.stop({ path: path.join(artifactDir, 'trace.zip') })
    await bobContext.tracing.stop({ path: path.join(artifactDir, 'bob-trace.zip') })
    await aliceContext.close()
    await bobContext.close()
  }
})
