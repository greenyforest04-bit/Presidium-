import { MatrixTransport } from './src/core/messaging/MatrixTransport.ts'

async function main() {
  const alice = new MatrixTransport('http://localhost:8008')
  const bob = new MatrixTransport('http://localhost:8008')

  const aSession = await alice.login('alice', 'alice-password')
  console.log('ALICE_SESSION', aSession)

  const aliceRoomId = await alice.createDirectConversation('@bob:presidium.local')
  console.log('ALICE_ROOM', aliceRoomId)

  const bSession = await bob.login('bob', 'bob-password')
  console.log('BOB_SESSION', bSession)

  await new Promise((resolve) => setTimeout(resolve, 5000))

  const rooms = bob['client']?.getRooms?.() ?? []
  console.log(
    'BOB_ROOMS',
    rooms.map((room) => ({
      roomId: room.roomId,
      myMembership: room.getMyMembership(),
      joined: room.getJoinedMembers().map((m) => m.userId),
    })),
  )

  const direct = bob['client']?.getRooms?.().find((room) => {
    const otherUser = room.getMember('@alice:presidium.local')
    return Boolean(otherUser && otherUser.membership === 'join')
  })
  console.log('DIRECT_VIA_MEMBER', direct?.roomId ?? 'none')

  const room = bob['client']?.getRoom(aliceRoomId)
  console.log('LOOKUP_ALICE_ROOM', room ? { roomId: room.roomId, membership: room.getMyMembership() } : 'no-room')
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
