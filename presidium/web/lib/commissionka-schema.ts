import {z} from 'zod';
import {CATEGORIES,MAX_PRICE_KOPECKS} from './commissionka';
const category=z.enum(CATEGORIES.map(([id])=>id) as [typeof CATEGORIES[number][0],...typeof CATEGORIES[number][0][]]);
const specsSchema=z.array(z.object({name:z.string().trim().min(1).max(50),value:z.string().trim().min(1).max(160)}).strict()).max(8).refine(x=>new Set(x.map(s=>s.name.toLowerCase())).size===x.length);
export const listingInput=z.object({
 action:z.literal('create'),id:z.string().uuid(),category,condition:z.enum(['used','new']),sellerType:z.enum(['private','manufacturer','company']),
 title:z.string().trim().min(3).max(120),description:z.string().trim().min(20).max(2500),city:z.string().trim().min(1).max(80),
 priceKopecks:z.number().int().min(1).max(MAX_PRICE_KOPECKS),photoIds:z.array(z.string().uuid()).min(1).max(10).refine(x=>new Set(x).size===x.length),
 specs:specsSchema,
 acceptedRules:z.literal(true),conditionDeclaration:z.boolean(),
}).strict().refine(x=>x.condition!=='used'||x.conditionDeclaration,{message:'Нужно подтверждение использования товара.',path:['conditionDeclaration']});
export const listingUpdateInput=z.object({
 action:z.literal('update'),listingId:z.string().uuid(),revision:z.number().int().positive(),
 category,condition:z.enum(['used','new']),
 title:z.string().trim().min(3).max(120),description:z.string().trim().min(20).max(2500),city:z.string().trim().min(1).max(80),
 specs:specsSchema,
}).strict();
export const marketAction=z.union([
 listingInput,
 listingUpdateInput,
 z.object({action:z.literal('moderateListing'),listingId:z.string().uuid(),revision:z.number().int().positive(),decision:z.enum(['approve','reject']),conditionVerified:z.boolean().optional(),rightToSellVerified:z.boolean().optional(),note:z.string().trim().max(500).optional()}).strict(),
 z.object({action:z.literal('favorite'),listingId:z.string().uuid(),saved:z.boolean()}).strict(),
 z.object({action:z.literal('price'),listingId:z.string().uuid(),revision:z.number().int().positive(),priceKopecks:z.number().int().min(1).max(MAX_PRICE_KOPECKS)}).strict(),
 z.object({action:z.literal('withdraw'),listingId:z.string().uuid(),revision:z.number().int().positive()}).strict(),
 z.object({action:z.literal('checkout'),listingId:z.string().uuid(),revision:z.number().int().positive(),idempotencyKey:z.string().uuid().optional()}).strict(),
 z.object({action:z.literal('paySandbox'),orderId:z.string().uuid(),idempotencyKey:z.string().uuid().optional()}).strict(),
 z.object({action:z.literal('orderStatus'),orderId:z.string().uuid(),status:z.enum(['shipped','completed','cancelled'])}).strict(),
 z.object({action:z.literal('dispute'),orderId:z.string().uuid(),reason:z.string().trim().min(10).max(1000)}).strict(),
 z.object({action:z.literal('resolveDispute'),disputeId:z.string().uuid(),decision:z.enum(['refund_buyer','release_seller','dismiss']),resolutionNote:z.string().trim().min(5).max(1000)}).strict(),
 z.object({action:z.literal('linkMatrix'),accessToken:z.string().min(1).max(4096)}).strict(),
 z.object({action:z.literal('unlinkMatrix')}).strict(),
]);
export type ListingInput=z.infer<typeof listingInput>;
export type ListingUpdateInput=z.infer<typeof listingUpdateInput>;
export type MarketAction=z.infer<typeof marketAction>;
