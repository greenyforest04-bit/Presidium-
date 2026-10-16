/** Original frontend endpoints adapted to the existing FastAPI contracts. */
export function productPath(path:string[]){return path.join('/')==='capsule-agent/task'?['capsule-agent']:path}
