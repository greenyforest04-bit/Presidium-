import {z} from 'zod';
import type {FeedPost,Story,PostAttachment} from './domain';
import {read} from './storage';

export const SOCIAL_MAX_MEDIA_BYTES = 25 * 1024 * 1024;
export const ALLOWED_SOCIAL_MIMES = [
 'image/jpeg','image/png','image/webp','image/gif',
 'video/mp4','video/webm','video/quicktime',
 'application/pdf','text/plain'
] as const;

const safeId = z.string().min(2).max(128).regex(/^[a-zA-Z0-9_-]+$/).refine(
 v => !v.startsWith('editorial-') && !['s1','s2','s3','draft'].includes(v),
 'Демонстрационные записи и черновики не переносятся на сервер как реальные публикации.'
);

// Reject any text or metadata containing Matrix private room/event/key markers
export function containsMatrixPrivateMaterial(value: string): boolean {
 if (!value) return false;
 return /presidium-matrix-sealed|m\.megolm\.v1|m\.olm\.v1|BEGIN MEGOLM SESSION|"access_token"\s*:|"_matrix\/client\//i.test(value);
}

export function verifySocialMediaBytes(bytes: Uint8Array, mime: string, kind: 'image'|'video'|'file'): string | null {
 if (!bytes || bytes.byteLength === 0) return 'Пустой файл не может быть загружен.';
 if (bytes.byteLength > SOCIAL_MAX_MEDIA_BYTES) return 'Размер файла превышает лимит 25 МБ.';
 if (!ALLOWED_SOCIAL_MIMES.includes(mime as typeof ALLOWED_SOCIAL_MIMES[number])) return 'Неподдерживаемый MIME-тип файла.';
 if (kind === 'image' && !mime.startsWith('image/')) return 'Категория image не соответствует MIME-типу.';
 if (kind === 'video' && !mime.startsWith('video/')) return 'Категория video не соответствует MIME-типу.';
 if (kind === 'file' && mime !== 'application/pdf' && mime !== 'text/plain') return 'Категория file допускает только PDF или текстовый документ.';
 const ascii = (start: number, end: number) => new TextDecoder('ascii').decode(bytes.subarray(start, end));
 if (mime === 'image/webp') {
  if (bytes.byteLength < 30 || ascii(0, 4) !== 'RIFF' || ascii(8, 12) !== 'WEBP') return 'Повреждённый заголовок WebP.';
 } else if (mime === 'image/png') {
  const pngSig = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
  if (bytes.byteLength < 24 || pngSig.some((b, i) => bytes[i] !== b)) return 'Повреждённый заголовок PNG.';
 } else if (mime === 'image/jpeg') {
  if (bytes.byteLength < 4 || bytes[0] !== 0xFF || bytes[1] !== 0xD8 || bytes[2] !== 0xFF) return 'Повреждённый заголовок JPEG.';
 } else if (mime === 'image/gif') {
  const hdr = bytes.byteLength >= 6 ? ascii(0, 6) : '';
  if (bytes.byteLength < 10 || (hdr !== 'GIF87a' && hdr !== 'GIF89a')) return 'Повреждённый заголовок GIF.';
 } else if (mime === 'video/mp4' || mime === 'video/quicktime') {
  if (bytes.byteLength < 12 || ascii(4, 8) !== 'ftyp') return 'Повреждённый контейнер MP4/MOV.';
 } else if (mime === 'video/webm') {
  if (bytes.byteLength < 4 || bytes[0] !== 0x1A || bytes[1] !== 0x45 || bytes[2] !== 0xDF || bytes[3] !== 0xA3) return 'Повреждённый заголовок WebM.';
 } else if (mime === 'application/pdf') {
  if (bytes.byteLength < 8 || ascii(0, 5) !== '%PDF-') return 'Повреждённый заголовок PDF.';
 } else if (mime === 'text/plain') {
  if (bytes.includes(0)) return 'Бинарные данные недопустимы в текстовом файле.';
  try {
   const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
   if (containsMatrixPrivateMaterial(text)) return 'Приватные данные Matrix запрещены в публичных файлах.';
  } catch {
   return 'Текстовый файл должен быть в кодировке UTF-8.';
  }
 }
 return null;
}

export const socialPostCreateInput = z.object({
 action: z.literal('createPost'),
 id: safeId,
 text: z.string().trim().max(5000).refine(v => !containsMatrixPrivateMaterial(v), 'Приватные ключи и служебные данные Matrix не могут публиковаться в социальной ленте.'),
 channelId: z.string().trim().max(128).optional(),
 mediaIds: z.array(safeId).max(10).default([]),
 topics: z.array(z.string().trim().min(1).max(40)).max(10).default([]),
}).strict().refine(x => x.text.length > 0 || x.mediaIds.length > 0, 'Публикация должна содержать текст или медиа.');

export const socialPostUpdateInput = z.object({
 action: z.literal('updatePost'),
 id: safeId,
 revision: z.number().int().positive(),
 text: z.string().trim().max(5000).refine(v => !containsMatrixPrivateMaterial(v), 'Приватные ключи и служебные данные Matrix не могут публиковаться в социальной ленте.'),
 mediaIds: z.array(safeId).max(10).default([]),
 topics: z.array(z.string().trim().min(1).max(40)).max(10).default([]),
}).strict().refine(x => x.text.length > 0 || x.mediaIds.length > 0, 'Публикация должна содержать текст или медиа.');

export const socialPostDeleteInput = z.object({
 action: z.literal('deletePost'),
 id: safeId,
 revision: z.number().int().positive().optional(),
}).strict();

export const socialStoryCreateInput = z.object({
 action: z.literal('createStory'),
 id: safeId,
 text: z.string().trim().max(1000).default('').refine(v => !containsMatrixPrivateMaterial(v), 'Приватные данные Matrix запрещены в историях.'),
 color: z.string().trim().min(1).max(24).default('blue'),
 background: z.string().trim().min(1).max(32).default('#17171c'),
 layout: z.enum(['grid','rows','columns']).default('grid'),
 duration: z.number().int().min(1).max(60).default(8),
 mediaIds: z.array(safeId).max(4).default([]),
 layers: z.array(z.object({
  id: z.string().min(1).max(64),
  kind: z.enum(['text','sticker']),
  text: z.string().min(1).max(300),
  x: z.number().min(0).max(100),
  y: z.number().min(0).max(100),
  size: z.number().min(1).max(40),
  rotation: z.number().min(-180).max(180),
  color: z.string().min(1).max(32),
  plate: z.boolean().optional(),
 }).strict()).max(12).default([]),
 poll: z.object({
  question: z.string().trim().min(1).max(100),
  options: z.array(z.string().trim().min(1).max(40)).min(2).max(4),
 }).strict().optional(),
 ttlHours: z.number().int().min(1).max(48).default(24),
}).strict().refine(x => x.text.length > 0 || x.mediaIds.length > 0 || x.layers.length > 0 || !!x.poll, 'История не может быть пустой.');

export const socialStoryDeleteInput = z.object({
 action: z.literal('deleteStory'),
 id: safeId,
}).strict();

export const socialActionSchema = z.union([
 socialPostCreateInput,
 socialPostUpdateInput,
 socialPostDeleteInput,
 socialStoryCreateInput,
 socialStoryDeleteInput,
]);

export type SocialAction = z.infer<typeof socialActionSchema>;
export type SocialFeedPage = { posts: FeedPost[]; stories: Story[]; nextCursor: string | null; viewerId: string | null };

export class SocialSyncError extends Error {
 constructor(message: string, public status: number, public code?: string) { super(message); }
}

async function socialFetch<T>(url: string, init?: RequestInit): Promise<T> {
 const r = await fetch(url, init);
 if (!r.headers.get('content-type')?.includes('application/json')) {
  throw new SocialSyncError('Сервис публикаций временно недоступен.', r.status, 'social_unavailable');
 }
 const body = await r.json() as T & { error?: string; code?: string };
 if (!r.ok) throw new SocialSyncError(body.error || 'Операция с публикацией не выполнена.', r.status, body.code);
 return body;
}

export const socialApi = {
 postDetail(id: string, signal?: AbortSignal): Promise<{ post: FeedPost }> {
  const p = new URLSearchParams({ view: 'post', id });
  return socialFetch<{ post: FeedPost }>('/api/social?' + p, { cache: 'no-store', signal });
 },
 feed(cursor?: string, signal?: AbortSignal): Promise<SocialFeedPage> {
  const p = new URLSearchParams();
  if (cursor) p.set('cursor', cursor);
  return socialFetch<SocialFeedPage>('/api/social' + (p.size ? '?' + p : ''), { cache: 'no-store', signal });
 },
 act<T>(action: SocialAction, signal?: AbortSignal): Promise<T> {
  return socialFetch<T>('/api/social', {
   method: 'POST',
   headers: { 'Content-Type': 'application/json' },
   body: JSON.stringify(action),
   signal,
  });
 },
 async uploadMedia(id: string, blob: Blob, fileName: string, kind: 'image'|'video'|'file', alt = '', signal?: AbortSignal): Promise<{ id: string }> {
  const p = new URLSearchParams({ id, name: fileName, kind, alt });
  return socialFetch<{ id: string }>('/api/social/media?' + p, {
   method: 'POST',
   headers: { 'Content-Type': blob.type || 'application/octet-stream' },
   body: blob,
   signal,
  });
 },
 async publishLocalPostToServer(post: FeedPost, signal?: AbortSignal): Promise<FeedPost> {
  if (post.demo) throw new SocialSyncError('Демонстрационные публикации не переносятся на сервер.', 400, 'demo_forbidden');
  const attachments: PostAttachment[] = post.attachments || [];
  const mediaIds: string[] = [];
  for (const att of attachments) {
   const raw = await read<Blob | { bytes: ArrayBuffer; mime: string }>('blobs', att.blobId);
   if (!raw) throw new SocialSyncError(`Локальный файл «${att.name}» не найден на устройстве.`, 404, 'blob_missing');
   const blob = raw instanceof Blob ? raw : new Blob([raw.bytes], { type: raw.mime || att.mime });
   await socialApi.uploadMedia(att.id, blob, att.name, att.kind, att.alt || '', signal);
   mediaIds.push(att.id);
  }
  const res = await socialApi.act<{ post: FeedPost }>({
   action: 'createPost',
   id: post.id,
   text: post.text,
   channelId: post.channelId,
   mediaIds,
   topics: post.topics || [],
  }, signal);
  return res.post;
 },
 async publishLocalStoryToServer(story: Story, signal?: AbortSignal): Promise<Story> {
  if (story.demo) throw new SocialSyncError('Демонстрационные истории не переносятся на сервер.', 400, 'demo_forbidden');
  const mediaIds: string[] = [];
  for (const asset of story.media || []) {
   const raw = await read<Blob | { bytes: ArrayBuffer; mime: string }>('blobs', asset.id);
   if (!raw) throw new SocialSyncError('Медиафайл истории не найден на устройстве.', 404, 'blob_missing');
   const blob = raw instanceof Blob ? raw : new Blob([raw.bytes], { type: raw.mime || (asset.kind === 'video' ? 'video/mp4' : 'image/jpeg') });
   await socialApi.uploadMedia(asset.id, blob, `story-${asset.id}`, asset.kind, '', signal);
   mediaIds.push(asset.id);
  }
  const res = await socialApi.act<{ story: Story }>({
   action: 'createStory',
   id: story.id,
   text: story.text || '',
   color: story.color || 'blue',
   background: story.background || '#17171c',
   layout: story.layout || 'grid',
   duration: Math.round(story.duration || 8),
   mediaIds,
   layers: story.layers || [],
   poll: story.poll,
   ttlHours: 24,
  }, signal);
  return res.story;
 }
};

export function isOwnSocialPost(post: FeedPost, viewerId: string | null | undefined, localPosts: FeedPost[] | undefined): boolean {
 if (post.demo) return false;
 if (post.syncStatus === 'server' || post.authorId) {
  return !!viewerId && post.authorId === viewerId;
 }
 return (localPosts || []).some(p => p.id === post.id && !p.demo);
}

export function isOwnSocialStory(story: Story, viewerId: string | null | undefined, localStories: Story[] | undefined): boolean {
 if (story.demo) return false;
 if (story.syncStatus === 'server' || story.authorId) {
  return !!viewerId && story.authorId === viewerId;
 }
 return (localStories || []).some(s => s.id === story.id && !s.demo);
}
