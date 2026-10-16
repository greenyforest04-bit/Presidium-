import { test } from 'node:test'
import assert from 'node:assert/strict'
import { MatrixTransport } from '../../src/core/messaging/MatrixTransport.ts'

const alice = '@alice:local', bob = '@bob:local'
function state(members = [alice, bob], encrypted = true) {
  return [
    ...members.map((id) => ({ type: 'm.room.member', state_key: id, content: { membership: 'join' } })),
    ...(encrypted ? [{ type: 'm.room.encryption', state_key: '', content: { algorithm: 'm.megolm.v1.aes-sha2' } }] : []),
  ]
}
function fixture(members = [alice, bob], encrypted = true, cryptoReady = true) {
  const transport = new MatrixTransport()
  const sent: unknown[] = []
  let currentState = state(members, encrypted)
  const client = {
    getUserId: () => alice,
    roomState: async () => currentState,
    getCrypto: () => ({ isEncryptionEnabledInRoom: async () => cryptoReady }),
    sendMessage: async (...args: unknown[]) => { sent.push(args); return { event_id: '$sent' } },
  }
  Object.assign(transport, { client })
  return { transport, sent, client, setState: (s: typeof currentState) => { currentState = s } }
}
test('history never represents a local pending event as an accepted server message',async()=>{
 const transport=new MatrixTransport();
 const event=(id:string)=>({getId:()=>id,getType:()=> 'm.room.message',isEncrypted:()=>false,isDecryptionFailure:()=>false,getSender:()=>alice,getContent:()=>({body:id}),getTs:()=>1});
 Object.assign(transport,{client:{getRoom:()=>({getLiveTimeline:()=>({getEvents:()=>[event('~local'),event('$accepted')]})})}});
 assert.deepEqual((await transport.getMessages('!room')).map(m=>m.eventId),['$accepted']);
});
test('never sends a direct message to a group, plaintext room, or unready crypto', async () => {
  for (const f of [fixture([alice, bob, '@eve:local']), fixture([alice, bob], false), fixture([alice, bob], true, false)]) {
    await assert.rejects(f.transport.sendTextMessage('!room:local', 'private', bob))
    assert.equal(f.sent.length, 0)
  }
})
test('requires the intended recipient and rechecks membership on every send', async () => {
  const f = fixture()
  await assert.rejects(f.transport.sendTextMessage('!room:local', 'private'))
  await assert.rejects(f.transport.sendTextMessage('!room:local', 'private', '@eve:local'))
  assert.equal(await f.transport.sendTextMessage('!room:local', 'private', bob), '$sent')
  f.setState(state([alice, bob, '@eve:local']))
  await assert.rejects(f.transport.sendTextMessage('!room:local', 'next private message', bob))
  assert.equal(f.sent.length, 1)
})
test('a stale m.direct entry never authorizes a group room', async () => {
  const f = fixture([alice, bob, '@eve:local'])
  let created = false
  Object.assign(f.client, {
    getAccountData: () => ({ getContent: () => ({ [bob]: ['!group:local'] }) }),
    getRooms: () => [{ roomId: '!group:local', getMyMembership: () => 'join', getLastActiveTimestamp: () => 1 }],
    createRoom: async () => { created = true; f.setState(state()); return { room_id: '!private:local' } },
    setAccountData: async () => undefined,
  })
  assert.equal(await f.transport.createDirectConversation(bob), '!private:local')
  assert.equal(created, true)
  assert.equal(f.sent.length, 0)
})
test('saved session tokens are scoped to homeserver and exact account', () => {
  const a = new MatrixTransport('https://one.example'), b = new MatrixTransport('https://two.example')
  const key = (t: MatrixTransport, user: string) => (t as unknown as { sessionKey: (u: string) => string }).sessionKey(user)
  assert.notEqual(key(a, 'alice'), key(b, 'alice'))
  assert.notEqual(key(a, 'Alice'), key(a, 'alice'))
})


test('group sends require explicit marker, joined membership and active crypto; groups cannot become direct rooms', async()=>{
  const f=fixture()
  await assert.rejects(f.transport.sendGroupMessage('!group:local','group text'))
  const group=[...state(),{type:'m.presidium.kind',state_key:'',content:{kind:'group'}}]
  f.setState(group)
  await assert.rejects(f.transport.sendTextMessage('!group:local','private',bob))
  assert.equal(await f.transport.sendGroupMessage('!group:local','group text'),'$sent')
  f.setState(group.map(e=>e.type==='m.room.member'&&e.state_key===alice?{...e,content:{membership:'leave'}}:e))
  await assert.rejects(f.transport.sendGroupMessage('!group:local','after removal'))
  assert.equal(f.sent.length,1)
  const unready=fixture([alice,bob],true,false);unready.setState(group)
  await assert.rejects(unready.transport.sendGroupMessage('!group:local','no crypto'))
  assert.equal(unready.sent.length,0)
})
