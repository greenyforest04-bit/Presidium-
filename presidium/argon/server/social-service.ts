import {AccountError} from '../lib/account';
import {AccountService} from './account-service';
import {requireFeature} from './subscription-service';
import {ALLOWED_SOCIAL_MIMES,SOCIAL_MAX_MEDIA_BYTES,containsMatrixPrivateMaterial,type SocialAction,type SocialFeedPage} from '../lib/social-sync';
import type {FeedPost,Story,PostAttachment,StoryAsset} from '../lib/domain';

type PostRow={id:string;author_id:string;author_name:string;channel_id:string|null;text:string;media_ids:string;topics:string;status:string;revision:number;request_digest:string;created_at:number;updated_at:number};
type StoryRow={id:string;author_id:string;author_name:string;text:string;color:string;background:string;layout:'grid'|'rows'|'columns';duration:number;media_json:string;layers_json:string;poll_json:string|null;status:string;revision:number;request_digest:string;created_at:number;expires_at:number;updated_at:number};
type MediaRow={id:string;owner_id:string;post_id:string|null;story_id:string|null;kind:'image'|'video'|'file';mime:string;file_name:string;byte_size:number;sha256:string;alt_text:string;created_at:number};

const selectPost=`SELECT sp.*,p.name AS author_name FROM social_posts sp JOIN profiles p ON p.user_id=sp.author_id`;
const selectStory=`SELECT ss.*,p.name AS author_name FROM social_stories ss JOIN profiles p ON p.user_id=ss.author_id`;

async function digest(value:string){
 return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),n=>n.toString(16).padStart(2,'0')).join('');
}

export class SocialService{
 constructor(private db:D1Database,private owner:string,private token?:string){}
 private async requireSession(){return new AccountService(this.db,this.owner).requireSession(this.token)}
 private async requirePaid(feature:'social'|'stories'){
  const session=await this.requireSession();
  await requireFeature(this.db,this.owner,feature);
  return session;
 }
 private guard(paid:boolean){
  return `EXISTS(SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL AND expires_at>?)${paid?" AND EXISTS(SELECT 1 FROM subscriptions WHERE user_id=? AND status='active' AND tier IN ('plus','business') AND period_start<=? AND period_end>?)":''}`;
 }
 private args(sessionId:string,paid:boolean,now:number){
  return [sessionId,this.owner,now,...(paid?[this.owner,now,now]:[])];
 }
 private formatPost(row:PostRow,mediaById:Map<string,MediaRow>):FeedPost{
  const ids=JSON.parse(row.media_ids) as string[];
  const attachments:PostAttachment[]=ids.flatMap(id=>{
   const m=mediaById.get(id);
   return m?[{id:m.id,blobId:m.id,kind:m.kind,name:m.file_name,mime:m.mime,size:m.byte_size,alt:m.alt_text}]:[];
  });
  const images=attachments.filter(a=>a.kind==='image').map(a=>'/api/social/media?id='+encodeURIComponent(a.id));
  return {
   id:row.id,
   author:row.author_name,
   authorId:row.author_id,
   text:row.text,
   createdAt:row.created_at,
   updatedAt:row.updated_at>row.created_at?row.updated_at:undefined,
   channelId:row.channel_id||undefined,
   topics:JSON.parse(row.topics) as string[],
   attachments,
   images:images.length?images:undefined,
   video:attachments.some(a=>a.kind==='video'),
   syncStatus:'server',
   serverRevision:row.revision,
  };
 }
 private formatStory(row:StoryRow):Story{
  const mediaIds=JSON.parse(row.media_json) as StoryAsset[];
  return {
   id:row.id,
   authorId:row.author_id,
   name:row.author_name,
   text:row.text,
   color:row.color,
   background:row.background,
   layout:row.layout,
   duration:row.duration,
   media:mediaIds,
   imageSrc:mediaIds[0]&&mediaIds[0].kind==='image'?'/api/social/media?id='+encodeURIComponent(mediaIds[0].id):undefined,
   layers:JSON.parse(row.layers_json),
   poll:row.poll_json?JSON.parse(row.poll_json):undefined,
   createdAt:row.created_at,
   expiresAt:row.expires_at,
   syncStatus:'server',
   serverRevision:row.revision,
  };
 }
 private async loadMediaMap(ids:string[]):Promise<Map<string,MediaRow>>{
  const unique=[...new Set(ids)];
  if(!unique.length)return new Map();
  const rows=await this.db.prepare(`SELECT * FROM social_media WHERE id IN (${unique.map(()=>'?').join(',')})`).bind(...unique).all<MediaRow>();
  return new Map(rows.results.map(r=>[r.id,r]));
 }
 async getPost(id:string):Promise<FeedPost>{
  const row=await this.db.prepare(selectPost+" WHERE sp.id=? AND sp.status='published'").bind(id).first<PostRow>();
  if(!row)throw new AccountError('Публикация не найдена или удалена.',404,'post_missing');
  const mediaMap=await this.loadMediaMap(JSON.parse(row.media_ids) as string[]);
  return this.formatPost(row,mediaMap);
 }
 async getStory(id:string,now=Date.now()):Promise<Story>{
  const row=await this.db.prepare(selectStory+" WHERE ss.id=? AND ss.status='active' AND ss.expires_at>?").bind(id,now).first<StoryRow>();
  if(!row)throw new AccountError('История не найдена или срок её действия истёк.',404,'story_missing');
  return this.formatStory(row);
 }
 async feed(cursor?:string,now=Date.now()):Promise<SocialFeedPage>{
  const where=["sp.status='published'"],args:(string|number)[]=[];
  if(cursor){
   try{
    if(cursor.length>256)throw Error();
    const decoded=JSON.parse(atob(cursor)) as {createdAt:number;id:string};
    if(!Number.isSafeInteger(decoded.createdAt)||typeof decoded.id!=='string'||!decoded.id)throw Error();
    where.push('(sp.created_at<? OR (sp.created_at=? AND sp.id<?))');
    args.push(decoded.createdAt,decoded.createdAt,decoded.id);
   }catch{throw new AccountError('Некорректный курсор ленты публикаций.',400)}
  }
  const [postRows,storyRows]=await Promise.all([
   this.db.prepare(selectPost+' WHERE '+where.join(' AND ')+' ORDER BY sp.created_at DESC, sp.id DESC LIMIT 21').bind(...args).all<PostRow>(),
   this.db.prepare(selectStory+" WHERE ss.status='active' AND ss.expires_at>? ORDER BY ss.created_at DESC LIMIT 50").bind(now).all<StoryRow>(),
  ]);
  const slice=postRows.results.slice(0,20),last=slice.at(-1);
  const nextCursor=postRows.results.length>20&&last?btoa(JSON.stringify({createdAt:last.created_at,id:last.id})):null;
  const allMediaIds=slice.flatMap(r=>JSON.parse(r.media_ids) as string[]);
  const mediaMap=await this.loadMediaMap(allMediaIds);
  return {
   posts:slice.map(r=>this.formatPost(r,mediaMap)),
   stories:storyRows.results.map(r=>this.formatStory(r)),
   nextCursor,
   viewerId:this.owner||null,
  };
 }
 async authorizeMediaUpload(id:string,sha256:string){
  await this.requirePaid('social');
  const existing=await this.db.prepare('SELECT * FROM social_media WHERE id=?').bind(id).first<MediaRow>();
  if(existing){
   if(existing.owner_id!==this.owner)throw new AccountError('Этот идентификатор медиафайла принадлежит другому пользователю.',403,'forbidden_media_owner');
   if(existing.sha256!==sha256)throw new AccountError('Идентификатор медиафайла уже занят другим содержимым.',409,'media_conflict');
   return {existing,idempotent:true};
  }
  return {existing:null,idempotent:false};
 }
 async registerMedia(input:{id:string;kind:'image'|'video'|'file';mime:string;fileName:string;byteSize:number;sha256:string;altText?:string},now=Date.now()):Promise<MediaRow>{
  const session=await this.requireSession();
  await requireFeature(this.db,this.owner,'social');
  if(!ALLOWED_SOCIAL_MIMES.includes(input.mime as typeof ALLOWED_SOCIAL_MIMES[number]))throw new AccountError('Неподдерживаемый тип медиафайла.',415,'unsupported_media');
  if(!Number.isSafeInteger(input.byteSize)||input.byteSize<=0||input.byteSize>SOCIAL_MAX_MEDIA_BYTES)throw new AccountError('Размер файла должен быть от 1 байта до 25 МБ.',413,'media_too_large');
  if(containsMatrixPrivateMaterial(input.fileName)||containsMatrixPrivateMaterial(input.altText||''))throw new AccountError('Приватные данные Matrix запрещены в публичных медиа.',400);
  const existing=await this.db.prepare('SELECT * FROM social_media WHERE id=?').bind(input.id).first<MediaRow>();
  if(existing){
   if(existing.owner_id!==this.owner||existing.sha256!==input.sha256)throw new AccountError('Идентификатор медиафайла уже занят другим вложением.',409,'media_conflict');
   return existing;
  }
  const guard=this.guard(true),args=this.args(session.id,true,now);
  await this.db.prepare(`INSERT INTO social_media(id,owner_id,kind,mime,file_name,byte_size,sha256,alt_text,created_at) SELECT ?,?,?,?,?,?,?,?,? WHERE ${guard}`).bind(input.id,this.owner,input.kind,input.mime,input.fileName.slice(0,160),input.byteSize,input.sha256,(input.altText||'').slice(0,300),now,...args).run();
  const saved=await this.db.prepare('SELECT * FROM social_media WHERE id=? AND owner_id=?').bind(input.id,this.owner).first<MediaRow>();
  if(!saved)throw new AccountError('Не удалось зарегистрировать медиафайл.',409);
  return saved;
 }
 async canReadMedia(mediaId:string,now=Date.now()):Promise<MediaRow>{
  const row=await this.db.prepare('SELECT * FROM social_media WHERE id=?').bind(mediaId).first<MediaRow>();
  if(!row)throw new AccountError('Медиафайл не найден.',404);
  if(row.post_id){
   const post=await this.db.prepare("SELECT status FROM social_posts WHERE id=? AND status='published'").bind(row.post_id).first<{status:string}>();
   if(post)return row;
  }
  if(row.story_id){
   const story=await this.db.prepare("SELECT status FROM social_stories WHERE id=? AND status='active' AND expires_at>?").bind(row.story_id,now).first<{status:string}>();
   if(story)return row;
  }
  // Unlinked or deleted-post media is strictly isolated to its owner with an active session
  if(row.owner_id!==this.owner)throw new AccountError('Доступ к чужому неопубликованному медиафайлу запрещён.',403,'forbidden_media');
  await this.requireSession();
  return row;
 }
 async act(action:SocialAction,now=Date.now()){
  if(action.action==='createPost'){
   const session=await this.requirePaid('social');
   const reqDigest=await digest(JSON.stringify(action));
   const existing=await this.db.prepare('SELECT * FROM social_posts WHERE id=?').bind(action.id).first<PostRow>();
   if(existing){
    if(existing.author_id!==this.owner||existing.request_digest!==reqDigest)throw new AccountError('Идентификатор публикации уже использован.',409,'post_id_conflict');
    if(existing.status==='deleted')throw new AccountError('Эта публикация была удалена.',409);
    return {post:await this.getPost(action.id),idempotent:true};
   }
   if(action.mediaIds.length){
    const mediaRows=await this.db.prepare(`SELECT * FROM social_media WHERE owner_id=? AND (post_id IS NULL OR post_id=?) AND story_id IS NULL AND id IN (${action.mediaIds.map(()=>'?').join(',')})`).bind(this.owner,action.id,...action.mediaIds).all<MediaRow>();
    if(mediaRows.results.length!==action.mediaIds.length)throw new AccountError('Загрузите все принадлежащие вам медиафайлы перед публикацией.',409,'media_missing_or_foreign');
   }
   const guard=this.guard(true),args=this.args(session.id,true,now);
   const stmts=[
    this.db.prepare(`INSERT INTO social_posts(id,author_id,channel_id,text,media_ids,topics,status,revision,request_digest,created_at,updated_at) SELECT ?,?,?,?,?,?,'published',1,?,?,? WHERE ${guard}`).bind(action.id,this.owner,action.channelId||null,action.text,JSON.stringify(action.mediaIds),JSON.stringify(action.topics),reqDigest,now,now,...args),
   ];
   if(action.mediaIds.length){
    stmts.push(this.db.prepare(`UPDATE social_media SET post_id=? WHERE owner_id=? AND post_id IS NULL AND id IN (${action.mediaIds.map(()=>'?').join(',')}) AND EXISTS(SELECT 1 FROM social_posts WHERE id=? AND author_id=?)`).bind(action.id,this.owner,...action.mediaIds,action.id,this.owner));
   }
   await this.db.batch(stmts);
   return {post:await this.getPost(action.id),idempotent:false};
  }
  if(action.action==='updatePost'){
   const session=await this.requirePaid('social');
   const current=await this.db.prepare('SELECT * FROM social_posts WHERE id=?').bind(action.id).first<PostRow>();
   if(!current||current.status!=='published')throw new AccountError('Публикация не найдена.',404);
   if(current.author_id!==this.owner)throw new AccountError('Редактировать публикацию может только её автор.',403,'forbidden_author');
   if(action.mediaIds.length){
    const mediaRows=await this.db.prepare(`SELECT * FROM social_media WHERE owner_id=? AND (post_id IS NULL OR post_id=?) AND story_id IS NULL AND id IN (${action.mediaIds.map(()=>'?').join(',')})`).bind(this.owner,action.id,...action.mediaIds).all<MediaRow>();
    if(mediaRows.results.length!==action.mediaIds.length)throw new AccountError('Некорректный список вложений публикации.',409,'media_missing_or_foreign');
   }
   const guard=this.guard(true),args=this.args(session.id,true,now);
   const res=await this.db.prepare(`UPDATE social_posts SET text=?,media_ids=?,topics=?,revision=revision+1,updated_at=? WHERE id=? AND author_id=? AND revision=? AND status='published' AND ${guard}`).bind(action.text,JSON.stringify(action.mediaIds),JSON.stringify(action.topics),now,action.id,this.owner,action.revision,...args).run();
   if(!res.meta.changes)throw new AccountError('Публикация была изменена на другом устройстве. Загрузите актуальную ревизию.',409,'revision_conflict');
   await this.db.prepare('UPDATE social_media SET post_id=NULL WHERE post_id=? AND owner_id=?').bind(action.id,this.owner).run();
   if(action.mediaIds.length)await this.db.prepare(`UPDATE social_media SET post_id=? WHERE owner_id=? AND id IN (${action.mediaIds.map(()=>'?').join(',')})`).bind(action.id,this.owner,...action.mediaIds).run();
   return {post:await this.getPost(action.id)};
  }
  if(action.action==='deletePost'){
   // Owner can always delete their own post even if paid subscription has expired
   const session=await this.requireSession();
   const current=await this.db.prepare('SELECT * FROM social_posts WHERE id=?').bind(action.id).first<PostRow>();
   if(!current)throw new AccountError('Публикация не найдена.',404);
   if(current.author_id!==this.owner)throw new AccountError('Удалить публикацию может только её автор.',403,'forbidden_author');
   if(current.status==='deleted')return {deleted:true,idempotent:true};
   const guard=this.guard(false),args=this.args(session.id,false,now);
   const revClause=action.revision!==undefined?' AND revision=?':'';
   const revArgs=action.revision!==undefined?[action.revision]:[];
   const res=await this.db.prepare(`UPDATE social_posts SET status='deleted',revision=revision+1,updated_at=? WHERE id=? AND author_id=?${revClause} AND ${guard}`).bind(now,action.id,this.owner,...revArgs,...args).run();
   if(!res.meta.changes)throw new AccountError('Публикация изменилась перед удалением. Обновите ленту.',409,'revision_conflict');
   return {deleted:true,idempotent:false};
  }
  if(action.action==='createStory'){
   const session=await this.requirePaid('stories');
   const reqDigest=await digest(JSON.stringify(action));
   const existing=await this.db.prepare('SELECT * FROM social_stories WHERE id=?').bind(action.id).first<StoryRow>();
   if(existing){
    if(existing.author_id!==this.owner||existing.request_digest!==reqDigest)throw new AccountError('Идентификатор истории уже использован.',409,'story_id_conflict');
    return {story:await this.getStory(action.id,now),idempotent:true};
   }
   let storyMedia:StoryAsset[]=[];
   if(action.mediaIds.length){
    const mediaRows=await this.db.prepare(`SELECT * FROM social_media WHERE owner_id=? AND post_id IS NULL AND (story_id IS NULL OR story_id=?) AND id IN (${action.mediaIds.map(()=>'?').join(',')})`).bind(this.owner,action.id,...action.mediaIds).all<MediaRow>();
    if(mediaRows.results.length!==action.mediaIds.length)throw new AccountError('Загрузите все собственные медиафайлы для истории.',409,'media_missing_or_foreign');
    storyMedia=mediaRows.results.map(m=>({id:m.id,kind:m.kind==='video'?'video':'image'}));
   }
   const expiresAt=now+action.ttlHours*3600*1000;
   const guard=this.guard(true),args=this.args(session.id,true,now);
   const stmts=[
    this.db.prepare(`INSERT INTO social_stories(id,author_id,text,color,background,layout,duration,media_json,layers_json,poll_json,status,revision,request_digest,created_at,expires_at,updated_at) SELECT ?,?,?,?,?,?,?,?,?,?,'active',1,?,?,?,? WHERE ${guard}`).bind(action.id,this.owner,action.text,action.color,action.background,action.layout,action.duration,JSON.stringify(storyMedia),JSON.stringify(action.layers),action.poll?JSON.stringify(action.poll):null,reqDigest,now,expiresAt,now,...args),
   ];
   if(action.mediaIds.length){
    stmts.push(this.db.prepare(`UPDATE social_media SET story_id=? WHERE owner_id=? AND story_id IS NULL AND id IN (${action.mediaIds.map(()=>'?').join(',')}) AND EXISTS(SELECT 1 FROM social_stories WHERE id=? AND author_id=?)`).bind(action.id,this.owner,...action.mediaIds,action.id,this.owner));
   }
   await this.db.batch(stmts);
   return {story:await this.getStory(action.id,now),idempotent:false};
  }
  // deleteStory: owner can always delete their own story even if subscription expired
  const session=await this.requireSession();
  const current=await this.db.prepare('SELECT * FROM social_stories WHERE id=?').bind(action.id).first<StoryRow>();
  if(!current)throw new AccountError('История не найдена.',404);
  if(current.author_id!==this.owner)throw new AccountError('Удалить историю может только её автор.',403,'forbidden_author');
  if(current.status==='deleted')return {deleted:true,idempotent:true};
  const guard=this.guard(false),args=this.args(session.id,false,now);
  await this.db.prepare(`UPDATE social_stories SET status='deleted',revision=revision+1,updated_at=? WHERE id=? AND author_id=? AND ${guard}`).bind(now,action.id,this.owner,...args).run();
  return {deleted:true,idempotent:false};
 }
}
