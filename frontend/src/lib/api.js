export async function request(path, options = {}, { demo = false, onUnauthorized } = {}) {
  if (demo) throw new Error('效果预览不会提交操作。请进入实际工作台。');
  const headers = {
    ...(options.body && !(options.body instanceof File) ? { 'Content-Type': 'application/json' } : {}),
    ...options.headers,
  };
  let response;
  try {
    response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  } catch {
    throw new Error('无法连接工作台。请检查网络；发送结果不明时，保留原文后重试。');
  }
  const isJSON = (response.headers.get('content-type') || '').includes('json');
  const data = isJSON ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') onUnauthorized?.();
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((item) => item.msg).join('；')
      : data?.detail;
    throw new Error(detail || `请求未完成（${response.status}）`);
  }
  return data;
}
export function makeRequestId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
