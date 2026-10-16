import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runLocalAudit} from '../../lib/presidium-agent';
import {seed} from '../../lib/presidium';
test('the original local Capsule agent excludes live Matrix messages from its sources',async()=>{
 const state=seed(),now=Date.now();
 state.messages=[{id:'$private',spaceId:'!matrix',text:'PRIVATE_NETWORK_TEXT',sender:'alice',own:true,createdAt:now,kind:'text'},
 {id:'note',spaceId:'saved',text:'Local note',sender:'alice',own:true,createdAt:now,kind:'text'}];
 const report=await runLocalAudit({state,mode:'messages',scopes:['messages'],now});
 assert.equal(JSON.stringify(report).includes('PRIVATE_NETWORK_TEXT'),false);
 assert.deepEqual(report.items.map(i=>i.text),['Local note']);
});
