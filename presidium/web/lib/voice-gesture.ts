// Gesture coordinates stay in viewport space while the microphone grows/moves.
export class VoiceGesture {
 private pointer?:{id:number;x:number;y:number;at:number;locked:boolean};
 begin(id:number,x:number,y:number,at:number){this.pointer={id,x,y,at,locked:false}}
 move(id:number,x:number,y:number,cancelDistance=96){
  const p=this.pointer;if(!p||p.id!==id||p.locked)return;
  const left=Math.max(0,p.x-x),up=Math.max(0,p.y-y);
  if(left>=cancelDistance&&left>up){this.pointer=undefined;return {action:'cancel' as const,left,up}}
  if(up>=80&&up>left){p.locked=true;return {action:'lock' as const,left:0,up:80}}
  return {action:'move' as const,left:Math.min(left,cancelDistance),up:Math.min(up,80)};
 }
 end(id:number,at:number,phase:string,elapsed:number){
  const p=this.pointer;if(!p||p.id!==id)return 'none';this.pointer=undefined;
  if(p.locked)return 'none';
  if(phase==='ready'||phase==='stopping')return 'none';
  if(phase!=='requesting'&&phase!=='recording')return 'cancel';
  // A quick tap is an accessible hands-free start, including the permission prompt.
  if(at-p.at<200)return 'lock';
  return phase==='recording'&&elapsed>=250?'send':'cancel';
 }
 cancel(id?:number){if(id!==undefined&&this.pointer?.id!==id)return false;const held=!!this.pointer&&!this.pointer.locked;this.pointer=undefined;return held}
}
