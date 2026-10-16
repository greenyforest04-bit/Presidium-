import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sameOrigin,secureRequest} from '../../lib/request-origin';
test('cookie writes accept the browser host, reject cross-site and absent origins',()=>{
 const req=(origin?:string)=>new Request('http://localhost:5173/api/session',{headers:{host:'127.0.0.1:5173',...(origin?{origin}:{})}});
 assert.equal(sameOrigin(req('http://127.0.0.1:5173')),true);
 assert.equal(sameOrigin(req('https://evil.example')),false);
 assert.equal(sameOrigin(req()),false);
 assert.equal(sameOrigin(req('http://localhost:5173')),false);
});

test('LAN HTTPS requires the configured protocol/host and sets Secure without weakening loopback',()=>{
 const previous=process.env.PRESIDIUM_PUBLIC_ORIGIN;process.env.PRESIDIUM_PUBLIC_ORIGIN='https://192.168.1.4:5443';
 try{
  const req=(host:string,origin:string)=>new Request('http://localhost:5173/api/auth/login',{headers:{host,origin}});
  assert.equal(sameOrigin(req('192.168.1.4:5443','https://192.168.1.4:5443')),true);
  assert.equal(sameOrigin(req('192.168.1.4:5443','http://192.168.1.4:5443')),false);
  assert.equal(sameOrigin(req('evil.example','https://192.168.1.4:5443')),false);
  assert.equal(sameOrigin(req('127.0.0.1:5173','http://127.0.0.1:5173')),true);
  assert.equal(secureRequest(req('192.168.1.4:5443','https://192.168.1.4:5443')),true);
  assert.equal(secureRequest(req('127.0.0.1:5173','http://127.0.0.1:5173')),false);
 }finally{if(previous===undefined)delete process.env.PRESIDIUM_PUBLIC_ORIGIN;else process.env.PRESIDIUM_PUBLIC_ORIGIN=previous}
});
