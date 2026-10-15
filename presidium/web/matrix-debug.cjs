const { MatrixTransport } = require('./src/core/messaging/MatrixTransport.ts');

(async () => {
  const alice = new MatrixTransport('http://localhost:8008');
  const bob = new MatrixTransport('http://localhost:8008');

  const aliceSession = await alice.login('alice', 'alice-password');
  console.log('ALICE_SESSION', aliceSession.userId, aliceSession.deviceId);

  const roomId = await alice.createDirectConversation('@bob:presidium.local');
  console.log('ALICE_ROOM', roomId);

  const bobSession = await bob.login('bob', 'bob-password');
  console.log('BOB_SESSION', bobSession.userId, bobSession.deviceId);

  await new Promise((resolve) => setTimeout(resolve, 10000));

  const bobRooms = bob['client']?.getRooms?.() ?? [];
  console.log('BOB_ROOMS', bobRooms.map((room) => ({ roomId: room.roomId, myMembership: room.getMyMembership(), members: room.getJoinedMembers().map((m) => m.userId) })));

  const room = bob['client']?.getRoom(roomId);
  console.log('BOB_ROOM_LOOKUP', room ? { roomId: room.roomId, membership: room.getMyMembership() } : 'missing');
})();
