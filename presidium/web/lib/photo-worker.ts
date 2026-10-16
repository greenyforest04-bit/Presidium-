import {encodePhoto} from './photo-codec';
self.onmessage=async event=>{try{self.postMessage({result:await encodePhoto(event.data.file,event.data.mode)})}catch{self.postMessage({error:true})}};
