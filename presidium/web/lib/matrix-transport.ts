// Original UI contract; implementation is the existing local Matrix transport.
import type {MatrixTransport as OriginalTransport} from './argon-matrix-contract';
import {supportedEngine} from './local-matrix';
export type * from './argon-matrix-contract';
class Adapter { constructor(){return supportedEngine()} }
export const MatrixTransport=Adapter as unknown as typeof OriginalTransport;
export type MatrixTransport=OriginalTransport;
