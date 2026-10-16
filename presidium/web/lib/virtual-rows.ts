/** Variable-height prefix sums, O(log n) measure/lookup. No DOM or React per-message tree. */
export class VirtualRows{
 readonly ids:string[];private indices=new Map<string,number>();private values:Float64Array;private tree:Float64Array;
 constructor(ids:string[],estimate=160,heights=new Map<string,number>()){this.ids=ids;ids.forEach((id,index)=>this.indices.set(id,index));this.values=new Float64Array(ids.length);this.tree=new Float64Array(ids.length+1);for(let i=1;i<=ids.length;i++){const height=Math.max(24,heights.get(ids[i-1])||estimate);this.values[i-1]=height;this.tree[i]+=height;const parent=i+(i&-i);if(parent<=ids.length)this.tree[parent]+=this.tree[i]}}
 indexOf(id:string){return this.indices.get(id)??-1}
 offset(index:number){let sum=0;for(let i=Math.min(this.ids.length,Math.max(0,index));i>0;i-=i&-i)sum+=this.tree[i];return sum}
 total(){return this.offset(this.ids.length)}
 measure(index:number,height:number){if(index<0||index>=this.ids.length||!Number.isFinite(height))return 0;const value=Math.max(24,height),delta=value-this.values[index];this.values[index]=value;for(let i=index+1;i<this.tree.length;i+=i&-i)this.tree[i]+=delta;return delta}
 at(offset:number){if(!this.ids.length)return 0;let index=0,sum=0;for(let bit=2**Math.floor(Math.log2(this.ids.length));bit>=1;bit=Math.floor(bit/2)){const next=index+bit;if(next<this.tree.length&&sum+this.tree[next]<=Math.max(0,offset)){sum+=this.tree[next];index=next}}return Math.min(index,this.ids.length-1)}
 range(top:number,height:number,overscan=8){const start=Math.max(0,this.at(top)-overscan),end=Math.min(this.ids.length,this.at(top+height)+overscan+1);return {start,end,before:this.offset(start),after:this.total()-this.offset(end)}}
}
