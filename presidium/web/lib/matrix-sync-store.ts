import {SyncAccumulator,type ISyncResponse} from 'matrix-js-sdk/lib/sync-accumulator';
import type {ISavedSync} from 'matrix-js-sdk/lib/store';
import type {IStoredClientOpts} from 'matrix-js-sdk/lib/client';
import type {IEvent} from 'matrix-js-sdk/lib/models/event';
import {SMART_SYNC} from './efficiency-config';
import type {MatrixVault} from './matrix-vault';
export async function encryptedSyncStore(sdk:typeof import('matrix-js-sdk'),vault:MatrixVault,onError:(e:unknown)=>void){
 const saved=await vault.get<ISavedSync>('sync');let options=await vault.get<IStoredClientOpts>('sdk-options');const accumulator=new SyncAccumulator({maxTimelineEntries:SMART_SYNC.savedTimeline});if(saved)accumulator.accumulate({next_batch:saved.nextBatch,rooms:saved.roomsData,account_data:{events:saved.accountData}} as ISyncResponse,true);
 return new class extends sdk.MemoryStore{
  private dirty=false;private lastSave=0;
  async isNewlyCreated(){return !saved}
  async getSavedSync(){return saved?accumulator.getJSON():null}
  async getSavedSyncToken(){return accumulator.getNextBatchToken()||null}
  async setSyncData(data:ISyncResponse){accumulator.accumulate(data);this.dirty=true}
  wantsSave(){return this.dirty&&Date.now()-this.lastSave>=1000}
  async save(_force:boolean){if(!this.dirty)return;const snapshot=accumulator.getJSON(true);this.dirty=false;try{await vault.put('sync',snapshot);this.lastSave=Date.now()}catch(e){this.dirty=true;onError(e)}}
  async getClientOptions(){return options}
  async storeClientOptions(value:IStoredClientOpts){await super.storeClientOptions(value);options=value;await vault.put('sdk-options',value)}
  async getPendingEvents(roomId:string){return await vault.get<Partial<IEvent>[]>('pending:'+roomId)||[]}
  async setPendingEvents(roomId:string,events:Partial<IEvent>[]){await super.setPendingEvents(roomId,events);await vault.put('pending:'+roomId,events)}
  async deleteAllData(){await vault.erase();await super.deleteAllData()}
 }();
}
