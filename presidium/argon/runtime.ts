/** Local module protocol. Only FastAPI may supply identity after Matrix whoami.
 * No socket, cookie login, trusted gateway headers, or browser-selectable user ID.
 */
import { createHash, randomUUID } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { SqliteD1 } from './sqlite.mjs';
import { z } from 'zod';
import { AccountError, updateInput } from './lib/account';
import { AccountService } from './server/account-service';
import { SpacesService } from './server/spaces-service';
import { spaceAction } from './lib/spaces';
import { SocialService } from './server/social-service';
import { socialActionSchema, verifySocialMediaBytes } from './lib/social-sync';
import { MarketplaceService } from './server/marketplace-service';
import { marketAction } from './lib/commissionka-schema';
import { subscription, aiUsage, reserveAI, requireFeature } from './server/subscription-service';
import { PLANS } from './lib/entitlements';
import { agentInput, agentConfigured, askCapsuleAgent } from './lib/capsule-agent';
import { verifyBilling, applyBilling } from './server/billing-webhook';

const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
const request = JSON.parse(await new Promise<string>((resolve, reject) => {
  let input = ''; process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => { input += chunk; if (input.length > 37_000_000) reject(Error('Input too large')); });
  process.stdin.on('end', () => resolve(input)); process.stdin.on('error', reject);
}));
const db = new SqliteD1(request.database), uid: string = request.identity?.user_id;
const token: string = request.sessionHash;
const body = request.body ?? {}, query = request.query ?? {}, now = Date.now();
const config = request.config ?? {};
const one = async <T = Record<string, unknown>>(sql: string, ...args: unknown[]): Promise<T | null> => db.prepare(sql).bind(...args).first();
const all = async (sql: string, ...args: unknown[]) => (await db.prepare(sql).bind(...args).all()).results;
const run = async (sql: string, ...args: unknown[]) => db.prepare(sql).bind(...args).run();
const parse = <S extends z.ZodTypeAny>(schema: S, value: unknown): z.output<S> => {
  const result = schema.safeParse(value);
  if (!result.success) throw new AccountError(result.error.issues[0]?.message ?? 'Некорректный запрос.', 400, 'validation_error');
  return result.data;
};
async function identityMirror() {
  if (!uid || !request.identity.device_id || !token) throw new AccountError('Matrix device is required.', 401);
  const id = 'matrix:' + hash(uid + '\0' + request.identity.device_id + '\0' + token);
  const existing = await one<{revoked_at:number|null}>('SELECT revoked_at FROM sessions WHERE id=?', id);
  if (existing?.revoked_at) throw new AccountError('Сеанс устройства отозван.', 401);
  await run('INSERT INTO users(id,created_at) VALUES(?,?) ON CONFLICT(id) DO NOTHING', uid, now);
  await run('INSERT INTO profiles(user_id,name,handle,bio,revision,updated_at) VALUES(?,?,NULL,\'\',1,?) ON CONFLICT(user_id) DO NOTHING', uid, uid.slice(1).split(':')[0], now);
  await run(`INSERT INTO sessions(id,user_id,token_hash,label,created_at,last_seen_at,expires_at) VALUES(?,?,?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash,last_seen_at=excluded.last_seen_at,expires_at=excluded.expires_at`,
    id, uid, hash(token), request.identity.device_id, now, now, now + 30 * 86400000);
}
async function spaceAccess(spaceId: string, write = false) {
  const row = await one<{role:string;kind:string}>(`SELECT m.role,s.kind FROM memberships m JOIN spaces s ON s.id=m.space_id WHERE m.space_id=? AND m.user_id=?`, spaceId, uid);
  if (!row || (write && row.kind === 'channel' && row.role === 'member')) throw new AccountError('Нет доступа к пространству.', 403);
}
async function postAccess(id: string) {
  const post = await social.getPost(id);
  if (post.channelId) await spaceAccess(post.channelId);
  return post;
}
const account = new AccountService(db, uid), social = new SocialService(db, uid, token);
const market = new MarketplaceService(db, uid, token, config, true);
const spaces = new SpacesService(db, uid, token);

async function media() {
  const id = parse(z.string().uuid(), query.id);
  const kind = request.area === 'marketplace' ? 'marketplace' : 'social';
  const variant = kind === 'marketplace' ? parse(z.enum(['full','thumb']), query.variant ?? 'full') : 'original';
  const directory = join(request.mediaDirectory, kind), file = join(directory, id + '-' + variant);
  if (request.method === 'GET') {
    let mime: string;
    if (kind === 'social') {
      const item = await social.canReadMedia(id);
      if (item.post_id && item.owner_id !== uid) await postAccess(item.post_id);
      mime = item.mime;
    }
    else {
      const row = await one<{owner_id:string;status:string}>(`SELECT i.owner_id,l.status FROM market_images i LEFT JOIN market_listings l ON l.id=i.listing_id WHERE i.id=?`, id);
      if (!row || (!['active','sold'].includes(row.status) && row.owner_id !== uid && !(await market.context()).capabilities.moderation)) throw new AccountError('Фото недоступно.',404);
      mime = 'image/webp';
    }
    return { binary: (await readFile(file)).toString('base64'), mime };
  }
  const bytes = Buffer.from(body.bytes, 'base64'), digest = hash(bytes);
  const mime = String(body.mime ?? '');
  await requireFeature(db, uid, kind === 'social' ? 'social' : 'marketplace');
  if (kind === 'social') {
    const mediaKind = parse(z.enum(['image','video','file']), body.kind);
    const error = verifySocialMediaBytes(bytes, mime, mediaKind);
    if (error) throw new AccountError(error, 415);
    await social.authorizeMediaUpload(id, digest);
    await social.registerMedia({id,kind:mediaKind,mime,fileName: String(body.name ?? 'media').slice(0,160),byteSize:bytes.length,sha256:digest});
  } else {
    if (mime !== 'image/webp' || bytes.length < 30 || bytes.length > (variant === 'full' ? 2097152 : 131072) || bytes.toString('ascii',0,4) !== 'RIFF' || bytes.toString('ascii',8,12) !== 'WEBP') throw new AccountError('Нужно обработанное фото WebP.',415);
    // Actual dimensions and metadata are checked/removed by the Python image adapter.
    const row = await one<{owner_id:string;listing_id:string|null;full_digest:string;thumb_digest:string|null}>('SELECT * FROM market_images WHERE id=?',id);
    if (row && row.owner_id !== uid) throw new AccountError('Чужое фото.',403);
    const known = variant === 'full' ? row?.full_digest : row?.thumb_digest;
    if (known && known !== digest) throw new AccountError('Идентификатор фото занят.',409);
    if (!known && row?.listing_id) throw new AccountError('Фото уже опубликовано.',409);
    if (variant === 'full' && !row) {
      const rate = await one<{n:number}>('SELECT COUNT(*) AS n FROM market_images WHERE owner_id=? AND created_at>?',uid,now-3600000);
      if ((rate?.n ?? 0) >= 100) throw new AccountError('Лимит фото за час.',429);
      await run('INSERT INTO market_images(id,owner_id,full_digest,size,created_at) VALUES(?,?,?,?,?)',id,uid,digest,bytes.length,now);
    } else if (variant === 'thumb') {
      if (!row) throw new AccountError('Сначала загрузите основное фото.',409);
      await run('UPDATE market_images SET thumb_digest=? WHERE id=? AND owner_id=?',digest,id,uid);
    }
  }
  await mkdir(directory, {recursive:true});
  // Atomic replace, with ownership/digest checked under the SQLite write lock.
  const temporary = file + '.' + randomUUID();
  await writeFile(temporary, bytes, {mode:0o600});
  const { rename } = await import('node:fs/promises'); await rename(temporary,file);
  return { id, stored:true };
}

async function dispatch(): Promise<unknown> {
  if (request.area === 'billing') {
    const verified = await verifyBilling(new Request('http://local/api/billing/webhook', {method:'POST',headers:request.headers,body:body.raw}), config.PRESIDIUM_BILLING_WEBHOOK_SECRET);
    await applyBilling(db,verified.event,verified.hash); return {accepted:true};
  }
  await identityMirror();
  if (query.media) return media();
  if (request.area === 'account') {
    if (request.method === 'GET') return { profile:await account.profile(), devices:await account.devices(await account.session(token)), identity:request.identity };
    if (body.action === 'revoke') { const id=parse(z.string().min(1).max(200),body.sessionId); return {revoked:await account.revoke(id,token)}; }
    return {profile:await account.update(parse(updateInput,body),body.revision,token)};
  }
  if (request.area === 'spaces') {
    if (request.method === 'GET') return query.id ? spaces.detail(query.id) : {spaces:await spaces.list()};
    return spaces.act(parse(spaceAction,body));
  }
  if (request.area === 'social') {
    if (request.method === 'GET') {
      const feed = await social.feed(query.cursor);
      const posts = [];
      for (const post of feed.posts) { try { if(post.channelId) await spaceAccess(post.channelId); posts.push(post); } catch {} }
      return {...feed,posts,following:await all('SELECT target_id AS id FROM social_follows WHERE user_id=?',uid)};
    }
    const action = parse(socialActionSchema, body);
    if (action.action === 'createPost' && action.channelId) await spaceAccess(action.channelId,true);
    if (action.action === 'updatePost' || action.action === 'deletePost') await postAccess(action.id);
    return social.act(action);
  }
  if (request.area === 'interactions') {
    const id=parse(z.string().min(1).max(200),query.postId ?? body.postId ?? body.targetId);
    if (request.method === 'GET') { await postAccess(id); return {comments:await all('SELECT c.*,p.name FROM social_comments c JOIN profiles p ON p.user_id=c.user_id WHERE post_id=? ORDER BY created_at',id)}; }
    if (body.action === 'follow') {
      if (!await one('SELECT 1 FROM profiles WHERE user_id=?',id)) throw new AccountError('Профиль не найден.',404);
      if (body.following) await run('INSERT INTO social_follows(user_id,target_id) VALUES(?,?) ON CONFLICT DO NOTHING',uid,id);
      else await run('DELETE FROM social_follows WHERE user_id=? AND target_id=?',uid,id);
      return {following:!!body.following};
    }
    await postAccess(id);
    const commentId=parse(z.string().uuid(),body.id);
    if (body.action === 'deleteComment') {
      await run('DELETE FROM social_comments WHERE id=? AND user_id=? AND post_id=?',commentId,uid,id); return {deleted:true};
    }
    const text=parse(z.string().trim().min(1).max(1000),body.text);
    const existing=await one<{user_id:string;text:string;post_id:string}>('SELECT * FROM social_comments WHERE id=?',commentId);
    if(existing && (existing.user_id!==uid || existing.text!==text || existing.post_id!==id)) throw new AccountError('Идентификатор комментария занят.',409);
    await run('INSERT INTO social_comments(id,post_id,user_id,text,created_at) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING',commentId,id,uid,text,now);
    return {id:commentId};
  }
  if (request.area === 'marketplace') {
    if (request.method === 'GET') {
      if (query.view === 'context') return market.context();
      if (query.view === 'detail') return market.detail(query.id);
      if (query.view === 'own') return market.own();
      if (query.view === 'orders') return market.orders();
      if (query.view === 'favorites') return market.favorites();
      if (query.view === 'moderation') return market.moderationQueue();
      return market.catalog(new URLSearchParams(query));
    }
    // The existing Matrix identity is already linked. Never accept another token.
    if (body.action === 'linkMatrix') throw new AccountError('Matrix уже является вашим аккаунтом.',400);
    if (body.action === 'resolveDispute') {
      if (!(await market.context()).capabilities.moderation) throw new AccountError('Нужны права модератора.',403);
      const proof=await one(`SELECT p.order_id FROM market_payment_provenance p JOIN market_disputes d ON d.order_id=p.order_id WHERE d.id=? AND p.kind='sandbox'`,body.disputeId);
      if(!proof) throw new AccountError('Нет подтверждения тестовой оплаты. Для реального или импортированного заказа требуется подтверждённое исполнение возврата/выплаты платёжным провайдером. Спор и обязательства сохранены.',409,'settlement_not_verified');
    }
    const result = await market.act(parse(marketAction,body));
    if(body.action==='paySandbox') {
      const payment=await one<{status:string}>('SELECT status FROM market_orders WHERE id=?',body.orderId);
      if(payment?.status==='paid_sandbox')await run("INSERT INTO market_payment_provenance(order_id,kind,created_at) VALUES(?,'sandbox',?) ON CONFLICT DO NOTHING",body.orderId,now);
    }
    await run(`INSERT INTO market_sellers(user_id,matrix_user_id,matrix_base_url) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET matrix_user_id=excluded.matrix_user_id,matrix_base_url=excluded.matrix_base_url`,uid,uid,config.MATRIX_PUBLIC_URL);
    return result;
  }
  if (request.area === 'subscription') return {subscription:await subscription(db,uid),usage:await aiUsage(db,uid,config),plans:PLANS,sandboxOrders:config.COMMISSIONKA_SANDBOX_ORDERS==='1'};
  if (request.area === 'capsule') {
    if (request.method === 'GET') return {entries:await all('SELECT * FROM capsule_entries WHERE user_id=? AND deleted_at IS NULL ORDER BY updated_at DESC',uid)};
    const action=parse(z.object({id:z.string().uuid(),revision:z.number().int().nonnegative(),title:z.string().trim().min(1).max(120),text:z.string().max(30000),kind:z.enum(['note','task']).default('note'),done:z.boolean().default(false),deleted:z.boolean().default(false)}).strict(),body);
    const existing=await one<{user_id:string;revision:number}>('SELECT user_id,revision FROM capsule_entries WHERE id=?',action.id);
    if (existing && existing.user_id!==uid) throw new AccountError('Чужая запись.',403);
    if ((existing?.revision ?? 0)!==action.revision) {
      const saved=await one<{title:string;text:string;kind:string;done:number;revision:number;deleted_at:number|null}>('SELECT * FROM capsule_entries WHERE id=?',action.id);
      if(saved && saved.revision===action.revision+1 && saved.title===action.title && saved.text===action.text && saved.kind===action.kind && !!saved.done===action.done && !!saved.deleted_at===action.deleted) return {entry:saved,idempotent:true};
      throw new AccountError('Запись изменена на другом устройстве. Ваш текст сохранён в форме.',409,'revision_conflict');
    }
    await run(`INSERT INTO capsule_entries(id,user_id,title,text,kind,done,revision,created_at,updated_at,deleted_at) VALUES(?,?,?,?,?,?,1,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,text=excluded.text,kind=excluded.kind,done=excluded.done,revision=capsule_entries.revision+1,updated_at=excluded.updated_at,deleted_at=excluded.deleted_at`,action.id,uid,action.title,action.text,action.kind,Number(action.done),now,now,action.deleted?now:null);
    return {entry:await one('SELECT * FROM capsule_entries WHERE id=?',action.id)};
  }
  if (request.area === 'capsule-agent') {
    const provider={url:config.CAPSULE_AGENT_URL,token:config.CAPSULE_AGENT_TOKEN};
    if(request.method==='GET') return {configured:agentConfigured(provider)};
    if(!agentConfigured(provider)) throw new AccountError('Задайте CAPSULE_AGENT_URL и CAPSULE_AGENT_TOKEN на сервере.',503,'agent_not_configured');
    const input=parse(agentInput,body);
    const reservation=await reserveAI(db,uid,input.requestId ?? '',config);
    // Reservation is committed before external I/O; retries cannot spend twice.
    db.connection.exec('COMMIT');
    return {answer:await askCapsuleAgent(input,provider,AbortSignal.timeout(30000)),usage:reservation.usage};
  }
  throw new AccountError('Неизвестный раздел.',404);
}
try {
  db.connection.exec('BEGIN IMMEDIATE');
  const value=await dispatch();
  if(db.connection.isTransaction) db.connection.exec('COMMIT');
  console.log(JSON.stringify({status:200,value}));
} catch(error) {
  if(db.connection.isTransaction) db.connection.exec('ROLLBACK');
  console.log(JSON.stringify({status:error instanceof AccountError?error.status:503,value:{error:error instanceof Error?error.message:'Сервис недоступен',code:error instanceof AccountError?error.code:'unavailable'}}));
} finally { db.close(); }
