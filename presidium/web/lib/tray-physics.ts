export const TRAY_HEIGHT=128;
export const clamp=(n:number,min=0,max=1)=>Math.max(min,Math.min(max,n));
export function rubberPull(distance:number){return distance<=0?0:1-Math.exp(-distance/110)}
export function snapTarget(progress:number,velocity:number){return velocity>300?1:velocity< -300?0:progress>=.5?1:0}
export function springStep(position:number,velocity:number,target:number,dt:number){const damping=2*.82*Math.sqrt(300);const v=velocity+(-300*(position-target)-damping*velocity)*Math.min(dt,.032);return {position:position+v*Math.min(dt,.032),velocity:v}}
