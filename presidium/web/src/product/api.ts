export class ProductError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status=status }
}
export async function productRequest<T>(token: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const value = await response.json()
  if (!response.ok) {
    const detail = value.detail ?? value
    throw new ProductError(typeof detail === 'string' ? detail : detail.error ?? 'Сервис недоступен', response.status)
  }
  return value as T
}
export async function upload(token: string, area: string, id: string, blob: Blob, query = '') {
  const response = await fetch(`/api/${area}/media?id=${encodeURIComponent(id)}${query}`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': blob.type }, body: blob })
  if (!response.ok) { const value=await response.json(); throw new Error(value.detail?.error ?? value.detail ?? 'Ошибка загрузки') }
}
export async function webp(file: File, edge: number): Promise<Blob> {
  if(file.size>25*1024*1024) throw new Error('Фото должно быть меньше 25 МБ')
  const image=await createImageBitmap(file)
  try {
    if(image.width*image.height>24_000_000) throw new Error('Слишком большое разрешение фото')
    const ratio=Math.min(1,edge/Math.max(image.width,image.height)), canvas=document.createElement('canvas')
    canvas.width=Math.max(1,Math.round(image.width*ratio));canvas.height=Math.max(1,Math.round(image.height*ratio))
    canvas.getContext('2d')!.drawImage(image,0,0,canvas.width,canvas.height)
    return await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('Обработка фото недоступна')),'image/webp',.82))
  } finally { image.close() }
}
