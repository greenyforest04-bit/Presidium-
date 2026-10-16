import {test} from 'node:test';
import assert from 'node:assert/strict';
import {productPath} from '../../lib/product-route';
test('the original Capsule task endpoint maps to the existing gateway without affecting media routes',()=>{
 assert.deepEqual(productPath(['capsule-agent','task']),['capsule-agent']);
 for(const path of [['account'],['marketplace','media'],['social','media'],['spaces']])assert.deepEqual(productPath(path),path);
});
