export interface ApiError {
  code: string
  message: string
}
export async function formatApiError(response: Response): Promise<string> {
  try {
    const body = (await response.clone().json()) as Partial<ApiError>
    if (body?.message) return body.message
  } catch {
    // 非 JSON 响应（如网关 502 HTML），回退到状态码文案
  }
  return `${response.status} ${response.statusText || '请求失败'}`
}