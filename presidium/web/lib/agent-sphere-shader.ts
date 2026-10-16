/** Layered luminous ribbons, rendered in a real sphere volume. No bitmap assets. */
export const sphereVertex=`attribute vec2 position;varying vec2 uv;void main(){uv=position*.5+.5;gl_Position=vec4(position,0.,1.);}`;
export const sphereFragment=`precision highp float;
varying vec2 uv;uniform float time;uniform float state;uniform float level;uniform float speech;uniform vec2 resolution;
mat2 rot(float a){return mat2(cos(a),-sin(a),sin(a),cos(a));}
void main(){
 vec2 p=(uv-.5)*2.8;p.x*=resolution.x/resolution.y;
 float t=time,audio=state==1.?level:0.,utterance=state==5.?speech:0.;
 vec3 red=vec3(1.,.025,.13),gold=vec3(1.,.3,.045),pink=vec3(.8,.075,.58),blue=vec3(.035,.62,1.);
 if(state==6.){blue=vec3(.06,.95,.64);gold=vec3(.64,.96,.16);}
 if(state==7.){red=vec3(.9,.14,.19);blue=vec3(.42,.28,.53);}
 if(state>=8.){red*=.5;gold*=.4;pink*=.45;blue*=.5;}
 vec3 ro=vec3(0.,0.,3.6),rd=normalize(vec3(p,-3.5));float radius=.99+audio*.055+utterance*.018;
 float b=dot(ro,rd),h=b*b-dot(ro,ro)+radius*radius;vec3 light=vec3(0.);float opacity=0.;
 if(h>0.){
  float near=-b-sqrt(h),far=-b+sqrt(h),stepSize=(far-near)/32.;
  for(int i=0;i<32;i++){
   vec3 q=ro+rd*(near+(float(i)+.5)*stepSize);
   q.xz=rot(t*.17)*q.xz;q.yz=rot(-.35+t*.11)*q.yz;
   float r=length(q),lat=q.y/max(r,.001),angle=atan(q.z,q.x);
   float wave=lat+.21*sin(angle*2.+t*.47)+.12*sin(angle*3.-t*.29+audio*.35);
   float band=exp(-abs(sin(wave*8.5+r*2.2-t*(state==4.?.7:.38)))*12.);
   float fine=exp(-abs(sin(wave*19.-r*5.+t*.24))*28.);
   float shells=exp(-abs(r-.9)*18.)+.75*exp(-abs(r-.72)*20.)+.55*exp(-abs(r-.51)*22.);
   float twist=sin(angle*3.+r*4.-t*.4)*.5+.5;
   vec3 tint=mix(red,gold,smoothstep(.35,.93,twist));
   tint=mix(tint,pink,smoothstep(-.1,.72,lat)*.7);
   tint=mix(tint,blue,smoothstep(.1,.8,q.x)*(.5+.5*twist));
   float d=(band+.28*fine)*shells*(.65+audio*.85+utterance*.26);
   float illumination=.55+.65*smoothstep(-.6,.65,q.z);
   light+=tint*d*stepSize*6.5*illumination*(1.-opacity*.35);
   opacity+=clamp(d*stepSize*.4,0.,.08)*(1.-opacity);
  }
  // A thin coloured rim defines the silhouette without filling it with white fog.
  float edge=pow(1.-sqrt(h)/radius,3.5);
  light+=mix(red,blue,smoothstep(-.5,.8,p.x))*edge*.75;
  opacity=max(opacity,edge*.8+.06);
 }
 // Two very thin orbits give depth around the sphere, never a milky halo.
 vec2 ring=p;ring=rot(.21+sin(t*.13)*.1)*ring;ring.y*=2.9;
 float orbit=exp(-abs(length(ring)-1.16)*135.)*(.32+.68*smoothstep(-.1,.7,p.y));
 light+=mix(red,gold,smoothstep(-1.,1.,p.x))*orbit*.35;
 vec2 ring2=rot(-.58)*p;ring2.y*=3.8;
 float orbit2=exp(-abs(length(ring2)-1.13)*160.);
 light+=mix(pink,blue,smoothstep(-1.,1.,p.x))*orbit2*.18;
 float glow=exp(-dot(p,p)*2.8)*.025;
 light+=red*glow;opacity=max(opacity,(orbit+orbit2)*.32+glow);
 // Exponential tone map preserves saturated reds and cyan even where ribbons intersect.
 vec3 colour=1.-exp(-light*1.8);
 float alpha=clamp(max(opacity,max(colour.r,max(colour.g,colour.b))),0.,1.);
 gl_FragColor=vec4(colour/max(alpha,.001),alpha);
}`;
