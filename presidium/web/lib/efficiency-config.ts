// Transport/cache policy is identical on every subscription tier.
export const SMART_SYNC={inboxTimeline:3,initialChat:30,historyPage:50,savedTimeline:30,localChat:1000,uiBatchMs:32,overscan:8,estimatedRow:160,pollTimeoutMs:30000} as const;
export const MEDIA_POLICY={economy:{maxEdge:1280,quality:.7,cacheBytes:64*1024**2,videoBitrate:600000},balanced:{maxEdge:1920,quality:.82,cacheBytes:128*1024**2,videoBitrate:900000},quality:{maxEdge:2560,quality:.9,cacheBytes:256*1024**2,videoBitrate:1400000}} as const;
export type MediaMode=keyof typeof MEDIA_POLICY;
export type EfficiencyPreferences={mode:MediaMode;cacheBytes:number;autoPhotos:boolean;autoVideos:boolean;wifiOnly:boolean};
export const defaultEfficiency:EfficiencyPreferences={mode:'balanced',cacheBytes:MEDIA_POLICY.balanced.cacheBytes,autoPhotos:false,autoVideos:false,wifiOnly:true};
export function efficiencyPreferences(value?:Partial<EfficiencyPreferences>):EfficiencyPreferences{return {mode:value?.mode&&Object.hasOwn(MEDIA_POLICY,value.mode)?value.mode:'balanced',cacheBytes:Math.max(0,Math.min(512*1024**2,Number.isFinite(value?.cacheBytes)?value!.cacheBytes!:defaultEfficiency.cacheBytes)),autoPhotos:value?.autoPhotos===true,autoVideos:value?.autoVideos===true,wifiOnly:value?.wifiOnly!==false}}
