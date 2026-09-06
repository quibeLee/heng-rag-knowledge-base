import {useEffect, useMemo, useRef, useState} from 'react'
import {
    Alert,
    Avatar,
    Button,
    Empty,
    Input,
    Space,
    Spin,
    Typography,
    message as antdMessage,
} from 'antd'
import {PartitionOutlined, PlusOutlined, RobotOutlined, SendOutlined, UserOutlined} from '@ant-design/icons'
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {createConversation, getConversation} from '@/client/sdk.gen'
import type { AgentStep, CitationRead, MessageRead, QueryRouteRead } from '@/client/types.gen'
import {streamChat, type ChatStreamEvent} from '@/api/chatStream'
import {AgentStepsPanel} from '@/components/AgentStepsPanel'
import {AgentGraphModal} from '@/components/AgentGraphModal'
import {gfmComponents} from '@/components/markdownComponents'
import {CitationList, type CitationListHandle} from '@/components/CitationList'
import {QueryRoutePanel} from '@/components/QueryRoutePanel'
import {formatApiError} from '@/utils/errors'

const {Title, Paragraph, Text} = Typography
const {TextArea} = Input
const STORAGE_KEY = 'rag.chat.conversation_id'
type AssistantStatus = 'streaming' | 'done' | 'error'

interface UiMessage {
    id: string
    role: 'user' | 'assistant'
    content: string
    citations: CitationRead[]
    queryRoute?: QueryRouteRead | null
    agentSteps?: AgentStep[] | null
    status?: AssistantStatus
    error?: string | null
}

function fromServerMessage(m: MessageRead): UiMessage {
    return {
        id: m.id,
        role: m.role === 'assistant' ? 'assistant' : 'user',
        content: m.content,
        citations: m.citations ?? [],
        queryRoute: m.query_route ?? null,
        agentSteps: m.agent_steps ?? null,
        status: 'done',
    }
}

export function ChatPage() {
    const queryClient = useQueryClient()
    const [conversationId, setConversationId] = useState<string | null>(
        () => localStorage.getItem(STORAGE_KEY),
    )
    const [draft, setDraft] = useState('')
    // 流式过程中的临时消息（只放在前端 state，结束后由历史接口回填正式 id）
    const [pendingMessages, setPendingMessages] = useState<UiMessage[]>([])
    const [isStreaming, setIsStreaming] = useState(false)
    const [graphModalOpen, setGraphModalOpen] = useState(false)
    const abortRef = useRef<AbortController | null>(null)
    const scrollRef = useRef<HTMLDivElement>(null)
    // 创建会话：第一次进入页面 / 点"新建对话"时调用
    const createMutation = useMutation({
        mutationFn: async () => {
            const res = await createConversation({body: {title: '新对话'}})
            return res.data!
        },
        onSuccess: (conversation) => {
            localStorage.setItem(STORAGE_KEY, conversation.id)
            setConversationId(conversation.id)
            setPendingMessages([])
            queryClient.removeQueries({queryKey: ['conversation']})
        },
    })
    // 没有 conversation_id 时自动创建一个
    useEffect(() => {
        if (!conversationId && !createMutation.isPending) {
            createMutation.mutate()
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [conversationId])
    // 拉取历史消息
    const historyQuery = useQuery({
        queryKey: ['conversation', conversationId],
        queryFn: async () => {
            const res = await getConversation({path: {conversation_id: conversationId!}})
            return res.data!
        },
        enabled: Boolean(conversationId),
    })
    // 历史消息变化 / 新对话切换 → 清空 pending（已并入历史）
    useEffect(() => {
        if (historyQuery.data) {
            setPendingMessages([])
        }
    }, [historyQuery.data])
    const allMessages = useMemo<UiMessage[]>(() => {
        const history = (historyQuery.data?.messages ?? []).map(fromServerMessage)
        return [...history, ...pendingMessages]
    }, [historyQuery.data, pendingMessages])
    // 最近一条带 Agent 轨迹的 assistant 消息：顶部"流程图"按钮展示本次实际执行路径
    const latestAgentSteps = useMemo(() => {
        for (let i = allMessages.length - 1; i >= 0; i--) {
            const msg = allMessages[i]
            if (msg.role === 'assistant' && msg.agentSteps?.length) return msg.agentSteps
        }
        return null
    }, [allMessages])
    // 自动滚到底部
    useEffect(() => {
        scrollRef.current?.scrollTo({top: scrollRef.current.scrollHeight, behavior: 'smooth'})
    }, [allMessages])
    // 组件卸载或新建对话时取消进行中的请求
    useEffect(() => {
        return () => {
            abortRef.current?.abort()
        }
    }, [])
    const handleNewConversation = () => {
        abortRef.current?.abort()
        createMutation.mutate()
    }
    const updateAssistant = (updater: (prev: UiMessage) => UiMessage) => {
        setPendingMessages((prev) => {
            if (prev.length === 0) return prev
            const lastIdx = prev.length - 1
            const last = prev[lastIdx]
            if (!last) return prev
            const next = prev.slice()
            next[lastIdx] = updater(last)
            return next
        })
    }
    const handleSend = async () => {
        const question = draft.trim()
        if (!question || !conversationId || isStreaming) return
        setDraft('')
        const userMsg: UiMessage = {
            id: `local-user-${Date.now()}`,
            role: 'user',
            content: question,
            citations: [],
            status: 'done',
        }
        const assistantMsg: UiMessage = {
            id: `local-assistant-${Date.now()}`,
            role: 'assistant',
            content: '',
            citations: [],
            status: 'streaming',
        }
        setPendingMessages((prev) => [...prev, userMsg, assistantMsg])
        setIsStreaming(true)
        const ctrl = new AbortController()
        abortRef.current = ctrl
        try {
            await streamChat({
                conversationId,
                question,
                signal: ctrl.signal,
                onEvent: (event: ChatStreamEvent) => {
                    switch (event.type) {
                        case 'start':
                            break
                        case 'query_route':
                            updateAssistant((prev) => ({...prev, queryRoute: event.queryRoute}))
                            break
                        case 'agent_steps':
                            updateAssistant((prev) => ({...prev, agentSteps: event.steps}))
                            break
                        case 'citations':
                            updateAssistant((prev) => ({...prev, citations: event.citations}))
                            break
                        case 'token':
                            updateAssistant((prev) => ({...prev, content: prev.content + event.delta}))
                            break
                        case 'end':
                            updateAssistant((prev) => ({...prev, status: 'done'}))
                            break
                        case 'error':
                            updateAssistant((prev) => ({...prev, status: 'error', error: event.message}))
                            break
                    }
                },
            })
            // 流正常结束 → 用后端历史替换前端 pending
            await queryClient.invalidateQueries({queryKey: ['conversation', conversationId]})
        } catch (err) {
            const fallback = err instanceof Response ? await formatApiError(err) : (err as Error).message
            updateAssistant((prev) => ({
                ...prev,
                status: 'error',
                error: fallback || '请求失败',
            }))
            antdMessage.error(fallback || '问答请求失败')
        } finally {
            setIsStreaming(false)
            abortRef.current = null
        }
    }
    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        // Shift+Enter 换行；Enter 发送
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            handleSend()
        }
    }
    return (
        <div style={{display: 'flex', flexDirection: 'column', height: 'calc(100vh - 160px)'}}>
            <Space style={{marginBottom: 12, justifyContent: 'space-between', display: 'flex'}}>
                <div>
                    <Title level={3} style={{marginBottom: 0}}>知识库问答</Title>
                    <Paragraph type="secondary" style={{marginBottom: 0}}>
                        基于已上传文档进行检索增强问答，引用来源可点击跳转原文档。
                    </Paragraph>
                </div>
                <Space>
                    <Button icon={<PartitionOutlined/>} onClick={() => setGraphModalOpen(true)}>
                        流程图
                    </Button>
                    <Button icon={<PlusOutlined/>} onClick={handleNewConversation} disabled={isStreaming}>
                        新建对话
                    </Button>
                </Space>
            </Space>
            <AgentGraphModal open={graphModalOpen} onClose={() => setGraphModalOpen(false)} steps={latestAgentSteps}/>
            <div
                ref={scrollRef}
                style={{
                    flex: 1,
                    overflowY: 'auto',
                    background: '#fff',
                    padding: 24,
                    borderRadius: 8,
                    border: '1px solid #f0f0f0',
                }}
            >
                {historyQuery.isLoading ? (
                    <Spin/>
                ) : allMessages.length === 0 ? (
                    <Empty description="还没有问题，在下方输入开始提问"/>
                ) : (
                    allMessages.map((msg) => <MessageBubble key={msg.id} message={msg}/>)
                )}
            </div>
            <div style={{marginTop: 12, display: 'flex', gap: 8}}>
                <TextArea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="输入你的问题，按 Enter 发送，Shift+Enter 换行"
                    autoSize={{minRows: 2, maxRows: 6}}
                    disabled={!conversationId || isStreaming}
                />
                <Button
                    type="primary"
                    icon={<SendOutlined/>}
                    onClick={handleSend}
                    loading={isStreaming}
                    disabled={!conversationId || !draft.trim()}
                >
                    发送
                </Button>
            </div>
        </div>
    )
}

const CITATION_HASH_PREFIX = '#cite-'

/** 把答案里的引用编号 `[N]` 改写成 markdown hash 链接，交给下方 CitationList 处理点击。
 *
 * 严格只匹配纯 `[N]`（不含反引号、不含尖括号），格式由后端 prompt 强约束。
 * 链接文本用 `[[N]](url)` 这种"成对方括号嵌套"形式 CommonMark 解析最稳定。
 */
function linkifyCitations(content: string, maxIndex: number, messageId: string): string {
    if (maxIndex <= 0) return content
    return content.replace(/\[(\d+)\]/g, (raw, num: string) => {
        const n = Number(num)
        if (n < 1 || n > maxIndex) return raw
        return `[[${n}]](${CITATION_HASH_PREFIX}${messageId}-${n})`
    })
}


function createMarkdownComponents(onCitationClick: (n: number) => void) {
    return {
        a: (props: React.ComponentProps<'a'>) => {
            const href = props.href ?? ''
            if (href.startsWith(CITATION_HASH_PREFIX)) {
                // 引用编号锚点：拦截默认跳转，转交给 CitationList 展开 + 滚动
                const n = Number(href.split('-').pop())
                return (
                    <a
                        {...props}
                        href={href}
                        onClick={(e) => {
                            e.preventDefault()
                            if (Number.isFinite(n)) onCitationClick(n)
                        }}
                    />
                )
            }
            return <a {...props} target="_blank" rel="noreferrer"/>
        },
        // 给 GFM 表格和 inline code 增加基础样式
        ...gfmComponents,
    }
}

interface MessageBubbleProps {
    message: UiMessage
}

function MessageBubble({message}: MessageBubbleProps) {
    const isUser = message.role === 'user'
    const citationRef = useRef<CitationListHandle>(null)
    const components = useMemo(
        () => createMarkdownComponents((n) => citationRef.current?.expandAndScroll(n)),
        [],
    )
    const renderedContent = useMemo(
        () => linkifyCitations(message.content, message.citations.length, message.id),
        [message.content, message.citations.length, message.id],
    )
    return (
        <div
            style={{
                display: 'flex',
                gap: 12,
                marginBottom: 24,
                flexDirection: isUser ? 'row-reverse' : 'row',
            }}
        >
            <Avatar
                icon={isUser ? <UserOutlined/> : <RobotOutlined/>}
                style={{background: isUser ? '#1677ff' : '#52c41a', flexShrink: 0}}
            />
            <div
                style={{
                    maxWidth: '78%',
                    background: isUser ? '#e6f4ff' : '#f6f6f6',
                    padding: '12px 16px',
                    borderRadius: 8,
                }}
            >
                {message.error ? (
                    <Alert type="error" message={message.error} style={{marginBottom: 8}}/>
                ) : null}
                {message.content ? (
                    isUser ? (
                        <Text style={{whiteSpace: 'pre-wrap'}}>{message.content}</Text>
                    ) : (
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                            {renderedContent}
                        </ReactMarkdown>
                    )
                ) : message.status === 'streaming' ? (
                    <Text type="secondary">
                        <Spin size="small"/> 正在思考...
                    </Text>
                ) : null}
                {!isUser && message.queryRoute ? (
                    <QueryRoutePanel queryRoute={message.queryRoute} />
                ) : null}
                {!isUser && message.agentSteps && message.agentSteps.length > 0 ? (
                    <AgentStepsPanel steps={message.agentSteps} />
                ) : null}
                {!isUser && message.citations.length > 0 ? (
                    <CitationList ref={citationRef} citations={message.citations} messageId={message.id}/>
                ) : null}
            </div>
        </div>
    )
}
