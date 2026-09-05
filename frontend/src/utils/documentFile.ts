const MARKDOWN_MIMES = new Set([
    'text/markdown',
    'text/x-markdown',
    'text/plain',
])
const PREVIEWABLE_MIMES = new Set([
    'application/pdf',
    'text/html',
    'application/xhtml+xml',
    ...MARKDOWN_MIMES,
])

export function buildDocumentFileUrl(
    documentId: string,
    options: { download?: boolean } = {},
): string {
    // 通过 vite proxy / nginx 转发到后端，无需写绝对地址
    const param = options.download ? '1' : '0'
    return `/api/documents/${documentId}/file?download=${param}`
}

/** 浏览器是否能内联预览该 mime 类型。DOCX 不行。 */
export function canPreviewInline(mimeType: string): boolean {
    return PREVIEWABLE_MIMES.has(mimeType)
}

export function isMarkdownMime(mimeType: string): boolean {
    return MARKDOWN_MIMES.has(mimeType)
}

export function isPdfMime(mimeType: string): boolean {
    return mimeType === 'application/pdf'
}

export function isHtmlMime(mimeType: string): boolean {
    return mimeType === 'text/html' || mimeType === 'application/xhtml+xml'
}