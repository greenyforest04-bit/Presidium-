export const PLANS={
 free:{title:'Free',priceRub:0,dailyAI:0,monthlyAI:0,configured:true},
 plus:{title:'Plus',priceRub:300,dailyAI:5,monthlyAI:50,configured:true},
 business:{title:'Business',priceRub:900,dailyAI:15,monthlyAI:150,configured:true},
 corporate:{title:'Corporate',priceRub:null,dailyAI:0,monthlyAI:0,configured:false},
} as const;
export type Plan=keyof typeof PLANS;
export type PaidFeature='social'|'stories'|'ai'|'business'|'marketplace';
export type Subscription={tier:Plan;periodStart?:number;periodEnd?:number;verified:boolean};
export type AIUsage={daily:number;monthly:number;dailyLimit:number;monthlyLimit:number;reservedMicroUsd:number;budgetMicroUsd:number};
export function canUse(tier:Plan,feature:PaidFeature){
 if(tier==='corporate')return false; // Explicitly unconfigured until separate server contract terms are provisioned
 return feature==='business'?tier==='business':tier==='plus'||tier==='business';
}
