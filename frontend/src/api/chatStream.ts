import {fetchEventSource} from '@microsoft/fetch-event-source'
import type {CitationRead, QueryRouteRead} from '@/client/types.gen'

export interface ChatStartEvent {
    type: 'start'
}

export interface ChatQueryRouteEvent {
  type: 'query_route'
  queryRoute: QueryRouteRead
}

export interface ChatCitationsEvent {
    type: 'citations'
    citations: CitationRead[]
}

export interface ChatTokenEvent {
    type: 'token'
    delta: string
}

export interface ChatEndEvent {
    type: 'end'
    message_id: string
    refused: boolean
}

export interface ChatErrorEvent {
    type: 'error'
    code: string
    message: string
}

export type ChatStreamEvent =
    | ChatStartEvent
    | ChatQueryRouteEvent
    | ChatCitationsEvent
    | ChatTokenEvent
    | ChatEndEvent
    | ChatErrorEvent

interface StreamChatParams {
    conversationId: string
    question: string
    signal?: AbortSignal
    onEvent: (event: ChatStreamEvent) => void
}

class FatalSseError extends Error {
}

export async function streamChat({
                                     conversationId,
                                     question,
                                     signal,
                                     onEvent,
                                 }: StreamChatParams): Promise<void> {
    await fetchEventSource(
        `/api/conversations/${conversationId}/chat`,
        {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question}),
            signal,
            // 默认会在 tab 切换到后台时关闭连接，问答场景不希望中断
            openWhenHidden: true,
            async onopen(response) {
                if (response.ok && response.headers.get('content-type')?.includes('text/event-stream')) {
                    return
                }
                const text = await response.text().catch(() => '')
                throw new FatalSseError(text || `HTTP ${response.status}`)
            },
            onmessage(msg) {
                if (!msg.event) return
                const data = msg.data ? JSON.parse(msg.data) : {}
                switch (msg.event) {
                    case 'message_start':
                        onEvent({type: 'start'})
                        break
                    case 'query_route':
                        onEvent({type: 'query_route', queryRoute: data as QueryRouteRead})
                        break
                    case 'citations':
                        onEvent({type: 'citations', citations: data.citations ?? []})
                        break
                    case 'token':
                        onEvent({type: 'token', delta: data.delta ?? ''})
                        break
                    case 'message_end':
                        onEvent({
                            type: 'end',
                            message_id: data.message_id,
                            refused: Boolean(data.refused),
                        })
                        break
                    case 'error':
                        onEvent({type: 'error', code: data.code ?? 'error', message: data.message ?? '请求失败'})
                        break
                }
            },
            onclose() {
                // 服务端正常关闭流；不抛错让上层走 finally 收尾
            },
            onerror(err) {
                throw err
            },
        },
    )
}