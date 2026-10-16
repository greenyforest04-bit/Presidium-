import {z} from 'zod';
const id=z.string().uuid();
const fields={name:z.string().trim().min(1,'Введите название.').max(80,'Название: максимум 80 символов.'),description:z.string().trim().max(500,'Описание: максимум 500 символов.')};
export const spaceAction=z.discriminatedUnion('action',[
 z.object({action:z.literal('create'),id,kind:z.enum(['group','channel']),...fields}),
 z.object({action:z.literal('update'),spaceId:id,revision:z.number().int().positive(),...fields}),
 z.object({action:z.literal('invite'),spaceId:id}),
 z.object({action:z.literal('join'),token:z.string().regex(/^[a-f0-9]{64}$/,'Введите полный код приглашения.')}),
 z.object({action:z.literal('revokeInvite'),spaceId:id,inviteId:id}),
 z.object({action:z.literal('role'),spaceId:id,userId:z.string().min(1).max(200),role:z.enum(['admin','member'])}),
 z.object({action:z.literal('remove'),spaceId:id,userId:z.string().min(1).max(200)}),
 z.object({action:z.literal('leave'),spaceId:id}),
]);
export type SpaceAction=z.infer<typeof spaceAction>;
export type ServerSpace={id:string;name:string;description:string;kind:'group'|'channel';role:'owner'|'admin'|'member';revision:number;createdAt:number;updatedAt:number;memberCount:number};
export type SpaceMember={userId:string;name:string;handle:string;role:ServerSpace['role'];joinedAt:number;self:boolean};
export type SpaceInvite={id:string;createdAt:number;expiresAt:number;status:'active'|'used'|'revoked'|'expired'};
export type SpaceDetail={space:ServerSpace;members:SpaceMember[];invites:SpaceInvite[]};
