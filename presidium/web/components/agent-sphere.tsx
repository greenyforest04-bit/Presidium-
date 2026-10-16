'use client';
import {useEffect,useRef,useState} from 'react';
import type {AgentState} from '@/lib/agent-state';
export type {AgentState} from '@/lib/agent-state';
const states:AgentState[]=['IDLE','LISTENING','TRANSCRIBING','THINKING','EXECUTING','SPEAKING','SUCCESS','ERROR','PAUSED','CANCELLED'];
import {sphereVertex as vertex,sphereFragment as fragment} from '@/lib/agent-sphere-shader';
export function AgentSphere({state='IDLE',level=0,reduceMotion=false,active=true,speechMark,className=''}:{state?:AgentState;level?:number|{current:number};reduceMotion?:boolean;active?:boolean;speechMark?:{current:number};className?:string}){
 const canvas=useRef<HTMLCanvasElement>(null),props=useRef({state,level,reduceMotion,active,speechMark}),[fallback,setFallback]=useState(false);props.current={state,level,reduceMotion,active,speechMark};
 useEffect(()=>{
  const node=canvas.current;if(!node)return;const gl=node.getContext('webgl',{alpha:true,antialias:false,powerPreference:'low-power',premultipliedAlpha:false});if(!gl){setFallback(true);return;}
  let disposed=false,frame=0,visible=true,lost=false,last=0,elapsed=0,smooth=0,quality=1,slow=0,speed=.65,painted=-1,paintedLevel=-1;const media=matchMedia('(prefers-reduced-motion: reduce)');
  function shader(type:number,source:string){const s=gl!.createShader(type)!;gl!.shaderSource(s,source);gl!.compileShader(s);if(!gl!.getShaderParameter(s,gl!.COMPILE_STATUS)){gl!.deleteShader(s);throw Error('Shader unavailable')}return s}
  let program:WebGLProgram,vs:WebGLShader,fs:WebGLShader,buffer:WebGLBuffer;
  try{vs=shader(gl.VERTEX_SHADER,vertex);fs=shader(gl.FRAGMENT_SHADER,(gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER,gl.HIGH_FLOAT)?.precision||0)>0?fragment:fragment.replace('precision highp float;','precision mediump float;'));program=gl.createProgram()!;gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('Link failed');gl.useProgram(program);buffer=gl.createBuffer()!;gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);const pos=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,2,gl.FLOAT,false,0,0);}catch{setFallback(true);return;}
  const uniforms={time:gl.getUniformLocation(program,'time'),state:gl.getUniformLocation(program,'state'),level:gl.getUniformLocation(program,'level'),speech:gl.getUniformLocation(program,'speech'),resolution:gl.getUniformLocation(program,'resolution')};
  function tick(now:number){if(disposed||lost)return;frame=requestAnimationFrame(tick);if(!visible||document.hidden||!props.current.active){last=now;return}const delta=now-last;if(delta<(quality<1?33:16))return;if(delta>42){slow++;if(slow>25){quality=.65;slow=0}}last=now;const reduced=media.matches||props.current.reduceMotion;const index=states.indexOf(props.current.state);speed+=((index>=8?0:index>=2&&index<=4?1.8:.65)-speed)*.12;if(!reduced)elapsed+=Math.min(delta,80)/1000*speed;
   const size=Math.min(440,Math.max(64,node!.clientWidth*Math.min(devicePixelRatio||1,1.5)*quality));const resized=node!.width!==Math.round(size)||node!.height!==Math.round(size);if(resized){node!.width=node!.height=Math.round(size);gl!.viewport(0,0,node!.width,node!.height)}
   const pulse=index===5&&props.current.speechMark?.current?Math.exp(-Math.max(0,now-props.current.speechMark.current)/180):0;const val=typeof props.current.level==='number'?props.current.level:props.current.level.current;if(!resized&&(reduced||index>=8)&&painted===index&&Math.abs(val+pulse-paintedLevel)<.015)return;painted=index;paintedLevel=val+pulse;smooth+=(Math.min(1,Math.max(0,val))-smooth)*.24;
   gl!.uniform1f(uniforms.time,elapsed);gl!.uniform1f(uniforms.state,states.indexOf(props.current.state));gl!.uniform1f(uniforms.level,smooth);gl!.uniform1f(uniforms.speech,pulse);gl!.uniform2f(uniforms.resolution,node!.width,node!.height);gl!.drawArrays(gl!.TRIANGLES,0,6);
  }
  const observer=new IntersectionObserver(([entry])=>{visible=entry.isIntersecting});observer.observe(node);const contextLost=(e:Event)=>{e.preventDefault();lost=true;cancelAnimationFrame(frame);setFallback(true)};node.addEventListener('webglcontextlost',contextLost);frame=requestAnimationFrame(tick);
  return()=>{disposed=true;cancelAnimationFrame(frame);observer.disconnect();node.removeEventListener('webglcontextlost',contextLost);gl.deleteBuffer(buffer);gl.deleteProgram(program);gl.deleteShader(vs);gl.deleteShader(fs)};
 },[]);
 return <div className={'pa-sphere '+className} data-state={state} data-motion={reduceMotion?'reduced':'full'}>{fallback?<div className="pa-sphere-fallback"><span/><small>Упрощённая графика</small></div>:<canvas ref={canvas} aria-hidden="true"/>}</div>;
}
