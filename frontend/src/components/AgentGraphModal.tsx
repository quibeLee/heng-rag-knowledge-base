import {useEffect, useState} from 'react'
import {Alert, Modal, Spin, Typography} from 'antd'
import {useQuery} from '@tanstack/react-query'
import {getAgentGraphMermaid} from '@/client/sdk.gen'
import type {AgentStep} from '@/client/types.gen'
import {buildExecutionMermaid} from '@/utils/agentFlow'
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
    /** 传入某条消息的 agent_steps 时，展示该次问答实际执行的路径；不传则展示静态图结构。 */
    steps?: AgentStep[] | null
}

/** Agentic RAG 流程图：优先渲染本次问答的实际执行路径（由 agent_steps 生成），
 * 无轨迹时回退到后端 draw_mermaid() 输出的静态图结构。 */
export function AgentGraphModal({open, onClose, steps}: AgentGraphModalProps) {
    const hasSteps = !!steps && steps.length > 0
    const {data: staticText, isLoading, error} = useQuery({
        queryKey: ['agentGraphMermaid'],
        queryFn: async () => {
            const res = await getAgentGraphMermaid()
            return res.data!.mermaid
        },
        enabled: open && !hasSteps,
        staleTime: Infinity,
    })
    const mermaidText = hasSteps ? buildExecutionMermaid(steps!) : staticText
    const [svg, setSvg] = useState<string | null>(null)
    const [renderError, setRenderError] = useState<string | null>(null)
    useEffect(() => {
        if (!open || !mermaidText) return
        let cancelled = false
        setSvg(null)
        setRenderError(null)
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
    const note = hasSteps
        ? '本图为本次问答实际执行的路径：每个 Round 对应一轮 plan_retrieval 决策与 retrieve → observe_context 观察；红色为提前拒答，绿色表示上下文充足，黄色表示不足后继续下一轮。'
        : '实线为主流程：normalize_query → route_query → plan_retrieval → retrieve → observe_context。虚线为条件分支：plan_retrieval 决策 refuse 时提前拒答；observe_context 判断上下文不足且未达最大轮数时回到 plan_retrieval 继续多轮检索。'
    return (
        <Modal
            title={hasSteps ? '本次问答执行流程' : 'Agentic RAG 流程图'}
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
                {note}
            </Paragraph>
        </Modal>
    )
}
