import type {FeedPost,State,Story} from './presidium';

// Editorial examples are separate from private records. A redesign never rewrites
// the user's posts, contacts, messages, story drafts or IndexedDB schema.
export const editorialImages = {
  portrait:'/editorial-portrait.png', city:'/editorial-city.png',
  woman:'/editorial-woman.png', technology:'/editorial-technology.png', music:'/editorial-music.png',
};
export const editorialPosts:FeedPost[] = [];

export function getFeedPosts(data:State):FeedPost[]{
  const personal=[...(data.feedPosts||[])].map(p=>p.demo?p:{...p,author:p.channelId?data.spaces.find(s=>s.id===p.channelId)?.name||p.author:data.profile}).sort((a,b)=>b.createdAt-a.createdAt);
  const channels:FeedPost[]=data.messages.filter(m=>data.spaces.some(s=>s.id===m.spaceId&&s.kind==='channel')).map(m=>({id:m.id,author:data.spaces.find(s=>s.id===m.spaceId)!.name,text:m.text,createdAt:m.createdAt,blobId:m.mime?.startsWith('image/')||m.kind==='video'?m.blobId:undefined,video:m.kind==='video',demo:m.demo})).sort((a,b)=>b.createdAt-a.createdAt);
  const ids=new Set<string>();
  return [...personal,...channels].filter(p=>{if(p.demo||ids.has(p.id))return false;ids.add(p.id);return true;});
}

export function matchesPost(post:FeedPost,query:string){
  const q=query.trim().toLocaleLowerCase('ru').replace(/^#/,'');
  return !q||[post.author,post.text,post.location,...(post.topics||[])].join(' ').toLocaleLowerCase('ru').includes(q);
}

export const editorialStories:Story[]=[];

export function activeStoryItems(data:State,now:number){
  return data.stories.filter(s=>!s.demo&&s.createdAt>now-86400000&&!['s1','s2','s3'].includes(s.id));
}
export function postTopics(data:State){
  const counts=new Map<string,number>();
  getFeedPosts(data).filter(p=>!data.feedHidden?.includes(p.id)).forEach(p=>{
    const tags=[...(p.topics||[]),...(p.text.match(/#[\p{L}\p{N}_]+/gu)||[]).map(t=>t.slice(1))];
    new Set(tags).forEach(t=>counts.set(t,(counts.get(t)||0)+1));
  });
  return [...counts].sort((a,b)=>b[1]-a[1]);
}
export const discoveryTiles=[
  {title:'Люди и истории',query:'Люди',image:editorialImages.portrait},
  {title:'Города и места',query:'Города',image:editorialImages.city},
  {title:'Музыка и события',query:'Музыка',image:editorialImages.music},
  {title:'Искусство видеть',query:'Фотография',image:editorialImages.woman},
  {title:'Технологии и дизайн',query:'Технологии',image:editorialImages.technology},
  {title:'Новые маршруты',query:'Путешествия',image:editorialImages.city},
];
