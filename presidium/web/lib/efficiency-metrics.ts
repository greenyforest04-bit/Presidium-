/** Bounded, metadata-only diagnostic counters; no URLs, tokens, room IDs or message bodies. */
export class EfficiencyMetrics{
 private rows:{kind:string;ms:number;status:number;bytes:number|null}[]=[];
 wrap(fetcher:typeof fetch):typeof fetch{return async(input,init)=>{const started=performance.now(),url=input instanceof Request?input.url:String(input),kind=/\/sync(?:\?|$)/.test(url)?'sync':/\/messages(?:\?|$)/.test(url)?'history':/\/media\//.test(url)?'media':'other';try{const response=await fetcher(input,init);const h=response.headers.get('content-length');this.push({kind,ms:performance.now()-started,status:response.status,bytes:h&&/^\d+$/.test(h)?Number(h):null});return response}catch(e){this.push({kind,ms:performance.now()-started,status:0,bytes:null});throw e}}}
 private push(row:{kind:string;ms:number;status:number;bytes:number|null}){this.rows.push(row);if(this.rows.length>256)this.rows.shift()}
 snapshot(){return {windowRequests:this.rows.length,declaredResponseBytes:this.rows.reduce((s,r)=>s+(r.bytes||0),0),unknownResponseSizes:this.rows.filter(r=>r.bytes===null).length,requests:this.rows.slice(),note:'Content-Length only; long-poll duration is not delivery latency; uploads via XHR not included.'}}
}
