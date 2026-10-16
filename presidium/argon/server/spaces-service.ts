import {AccountError} from '../lib/account';
import {AccountService} from './account-service';
import type {ServerSpace,SpaceDetail,SpaceAction} from '../lib/spaces';
const activeSession='EXISTS(SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL AND expires_at>?)';
const authority=(roles:string)=>`EXISTS(SELECT 1 FROM memberships AS actor WHERE actor.space_id=spaces.id AND actor.user_id=? AND actor.role IN (${roles}))`;
const selectSpace=`SELECT spaces.id,name,description,kind,revision,created_at AS createdAt,updated_at AS updatedAt,m.role,(SELECT COUNT(*) FROM memberships WHERE space_id=spaces.id) AS memberCount FROM spaces JOIN memberships m ON m.space_id=spaces.id WHERE m.user_id=?`;
async function hash(token:string){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(token))),v=>v.toString(16).padStart(2,'0')).join('')}
export class SpacesService{
 constructor(private db:D1Database,private userId:string,private token?:string){}
 private async session(){return new AccountService(this.db,this.userId).requireSession(this.token)}
 async list(){await this.session();return (await this.db.prepare(selectSpace+' ORDER BY updated_at DESC LIMIT 100').bind(this.userId).all<ServerSpace>()).results}
 async detail(id:string):Promise<SpaceDetail>{
  await this.session();const space=await this.db.prepare(selectSpace+' AND spaces.id=?').bind(this.userId,id).first<ServerSpace>();if(!space)throw new AccountError('Пространство недоступно или вы больше не участник.',404);
  const members=(await this.db.prepare('SELECT m.user_id AS userId,p.name,COALESCE(p.handle,\'\') AS handle,m.role,m.joined_at AS joinedAt FROM memberships m JOIN profiles p ON p.user_id=m.user_id WHERE m.space_id=? ORDER BY CASE m.role WHEN \'owner\' THEN 0 WHEN \'admin\' THEN 1 ELSE 2 END,p.name LIMIT 500').bind(id).all<SpaceDetail['members'][number]>()).results.map(m=>({...m,self:m.userId===this.userId}));
  const invites=space.role==='member'?[]:(await this.db.prepare('SELECT id,created_at AS createdAt,expires_at AS expiresAt,CASE WHEN revoked_at IS NOT NULL THEN \'revoked\' WHEN used_by IS NOT NULL THEN \'used\' WHEN expires_at<=? THEN \'expired\' ELSE \'active\' END AS status FROM space_invites WHERE space_id=? ORDER BY created_at DESC LIMIT 30').bind(Date.now(),id).all<SpaceDetail['invites'][number]>()).results;
  return {space,members,invites};
 }
 async act(input:SpaceAction){
  const session=await this.session(),now=Date.now(),sessionArgs=[session.id,this.userId,now];
  const authorized=(roles="'owner','admin'")=>`EXISTS(SELECT 1 FROM spaces WHERE spaces.id=? AND ${authority(roles)}) AND ${activeSession}`;
  const change=async(sql:string,args:unknown[])=>{const r=await this.db.prepare(sql).bind(...args).run();if(!r.meta.changes){await this.session();throw new AccountError('Операция недоступна: проверьте роль или обновите пространство.',409)}return r};
  if(input.action==='create'){
   const existing=await this.db.prepare(selectSpace+' AND spaces.id=?').bind(this.userId,input.id).first<ServerSpace>();if(existing)return {spaceId:existing.id};
   const results=await this.db.batch([
    this.db.prepare(`INSERT INTO spaces(id,owner_id,kind,name,description,created_at,updated_at) SELECT ?,?,?,?,?,?,? WHERE ${activeSession} AND (SELECT COUNT(*) FROM memberships WHERE user_id=?)<100`).bind(input.id,this.userId,input.kind,input.name,input.description,now,now,...sessionArgs,this.userId),
    this.db.prepare(`INSERT INTO memberships(id,space_id,user_id,role,joined_at) SELECT ?,?,?,'owner',? WHERE EXISTS(SELECT 1 FROM spaces WHERE id=? AND owner_id=?)`).bind(crypto.randomUUID(),input.id,this.userId,now,input.id,this.userId)
   ]);if(!results[0].meta.changes){await this.session();throw new AccountError('Достигнут лимит: 100 пространств на аккаунт.',409);}return {spaceId:input.id};
  }
  if(input.action==='join'){
   const tokenHash=await hash(input.token),membershipId=crypto.randomUUID();
   const valid=`token_hash=? AND revoked_at IS NULL AND used_by IS NULL AND expires_at>? AND ${activeSession}`;
   const results=await this.db.batch([
    this.db.prepare(`INSERT INTO memberships(id,space_id,user_id,role,joined_at,source_invite_id) SELECT ?,space_id,?,'member',?,id FROM space_invites WHERE ${valid} AND NOT EXISTS(SELECT 1 FROM memberships WHERE space_id=space_invites.space_id AND user_id=?) AND (SELECT COUNT(*) FROM memberships WHERE space_id=space_invites.space_id)<500 AND (SELECT COUNT(*) FROM memberships WHERE user_id=?)<100`).bind(membershipId,this.userId,now,tokenHash,now,...sessionArgs,this.userId,this.userId),
    this.db.prepare(`UPDATE space_invites SET used_by=? WHERE token_hash=? AND used_by IS NULL AND EXISTS(SELECT 1 FROM memberships WHERE id=? AND source_invite_id=space_invites.id)`).bind(this.userId,tokenHash,membershipId)
   ]);
   if(!results[0].meta.changes){const joined=await this.db.prepare('SELECT m.space_id AS id FROM memberships m JOIN space_invites i ON i.id=m.source_invite_id WHERE i.token_hash=? AND i.used_by=? AND m.user_id=?').bind(tokenHash,this.userId,this.userId).first<{id:string}>();if(joined)return {spaceId:joined.id};throw new AccountError('Код недействителен, использован, истёк, вы уже участник или достигнут лимит участников/пространств.',409)}
   const row=await this.db.prepare('SELECT space_id AS id FROM memberships WHERE id=?').bind(membershipId).first<{id:string}>();return {spaceId:row!.id};
  }
  if(input.action==='update'){
   await change(`UPDATE spaces SET name=?,description=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND ${authority("'owner','admin'")} AND ${activeSession}`,[input.name,input.description,now,input.spaceId,input.revision,this.userId,...sessionArgs]);return {spaceId:input.spaceId};
  }
  if(input.action==='invite'){
   const token=Array.from(crypto.getRandomValues(new Uint8Array(32)),v=>v.toString(16).padStart(2,'0')).join(''),inviteId=crypto.randomUUID();
   await change(`INSERT INTO space_invites(id,space_id,token_hash,created_by,created_at,expires_at) SELECT ?,?,?,?,?,? WHERE ${authorized()} AND (SELECT COUNT(*) FROM space_invites WHERE space_id=? AND revoked_at IS NULL AND used_by IS NULL AND expires_at>?)<30`,[inviteId,input.spaceId,await hash(token),this.userId,now,now+86400000,input.spaceId,this.userId,...sessionArgs,input.spaceId,now]);return {spaceId:input.spaceId,token};
  }
  if(input.action==='revokeInvite'){
   await change(`UPDATE space_invites SET revoked_at=? WHERE id=? AND space_id=? AND revoked_at IS NULL AND used_by IS NULL AND ${authorized()}`,[now,input.inviteId,input.spaceId,input.spaceId,this.userId,...sessionArgs]);return {spaceId:input.spaceId};
  }
  if(input.action==='role'){
   await change(`UPDATE memberships SET role=? WHERE space_id=? AND user_id=? AND role<>'owner' AND ${authorized("'owner'")}`,[input.role,input.spaceId,input.userId,input.spaceId,this.userId,...sessionArgs]);return {spaceId:input.spaceId};
  }
  if(input.action==='remove'){
   await change(`DELETE FROM memberships WHERE space_id=? AND user_id=? AND role<>'owner' AND ${authorized("'owner'")}`,[input.spaceId,input.userId,input.spaceId,this.userId,...sessionArgs]);return {spaceId:input.spaceId};
  }
  await change(`DELETE FROM memberships WHERE space_id=? AND user_id=? AND role<>'owner' AND ${activeSession}`,[input.spaceId,this.userId,...sessionArgs]);return {left:true};
 }
}
