import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
await build({entryPoints:[resolve(root,'argon/runtime.ts')],outfile:resolve(root,'presidium/features/argon-runtime.mjs'),bundle:true,platform:'node',target:'node24',format:'esm',nodePaths:[resolve(root,'web/node_modules')],logLevel:'info'});
