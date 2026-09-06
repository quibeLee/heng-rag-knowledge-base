import {useEffect, useState} from 'react'
import {Alert, Modal, Spin, Typography} from 'antd'
import {useQuery} from '@tanstack/react-query'
import {getAgentGraphMermaid} from '@/client/sdk.gen'
const {Paragraph, Text} = Typography

// 动态加载 mermaid（体积较大），首包不引入；模块级缓存避免重复初始化
let mermaidPromise: Promise<typeof import('mermaid')['default']> | null = null
function loadMermaid() {
    mermaidPromise ??= import('mermaid').then((m) => {
        m.default.initialize({startOnLoad: false})
        return m.default
    })
    return mermaidPromise
}

interface AgentGraphModalProps {
    open: boolean
    onClose: () => void
}

/** Agentic RAG 图结构可视化：后端 draw_mermaid() 输出 Mermaid 文本，这里渲染为 SVG。 */
export function AgentGraphModal({open, onClose}: AgentGraphModalProps) {
    const {data: mermaidText, isLoading, error} = useQuery({
        queryKey: ['agentGraphMermaid'],
        queryFn: async () => {
            const res = await getAgentGraphMermaid()
            return res.data!.mermaid
        },
        enabled: open,
        staleTime: Infinity,
    })
    const [svg, setSvg] = useState<string | null>(null)
    const [renderError, setRenderError] = useState<string | null>(null)
    useEffect(() => {
        if (!open || !mermaidText) return
        let cancelled = false
        // render id 必须唯一，重复 id 会导致二次打开时渲染失败
        loadMermaid()
            .then((m) => m.render(`agent-graph-${Date.now()}`, mermaidText))
            .then(({svg: rendered}) => {
                if (!cancelled) setSvg(rendered)
            })
            .catch((err) => {
                if (!cancelled) setRenderError(err instanceof Error ? err.message : String(err))
            })
        return () => {
            cancelled = true
        }
    }, [open, mermaidText])
    return (
        <Modal
            title="Agentic RAG 流程图"
            open={open}
            onCancel={onClose}
            footer={null}
            width={860}
            centered
        >
            {isLoading ? (
                <div style={{padding: 48, textAlign: 'center'}}>
                    <Spin/>
                </div>
            ) : error ? (
                <Alert type="error" message="流程图加载失败" description={(error as Error).message}/>
            ) : renderError ? (
                <Alert
                    type="warning"
                    message="Mermaid 渲染失败，显示源文本"
                    description={renderError}
                    style={{marginBottom: 12}}
                />
            ) : null}
            {svg ? (
                <div
                    style={{overflow: 'auto', textAlign: 'center', padding: '12px 0'}}
                    dangerouslySetInnerHTML={{__html: svg}}
                />
            ) : mermaidText && renderError ? (
                <Paragraph>
                    <Text copyable style={{whiteSpace: 'pre-wrap', fontSize: 12}}>{mermaidText}</Text>
                </Paragraph>
            ) : null}
            <Paragraph type="secondary" style={{marginBottom: 0, fontSize: 12}}>
                实线为主流程：normalize_query → route_query → plan_retrieval → retrieve → observe_context。
                虚线为条件分支：plan_retrieval 决策 refuse 时提前拒答；observe_context 判断上下文不足且未达最大轮数时回到 plan_retrieval 继续多轮检索。
            </Paragraph>
        </Modal>
    )
}
