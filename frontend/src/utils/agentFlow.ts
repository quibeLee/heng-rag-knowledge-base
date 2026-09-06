import type {AgentStep} from '@/client/types.gen'

export const ACTION_META: Record<AgentStep['action'], { color: string; label: string }> = {
    initial: {color: 'default', label: '初始检索'},
    proceed: {color: 'green', label: '继续生成'},
    rewrite_query: {color: 'blue', label: '改写 Query'},
    switch_route: {color: 'purple', label: '切换策略'},
    refuse: {color: 'red', label: '提前拒答'},
}

/** Mermaid 节点标签内的特殊字符转义，防止 query / reason 文本破坏语法 */
function escapeLabel(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
}

/** 根据一次问答的 agent_steps 轨迹，生成实际执行路径的 Mermaid 文本。
 *
 * 路径推导与后端 graph.py 的边定义一致：
 * normalize_query → route_query 后进入每一轮 plan_retrieval；
 * action=refuse 时提前结束，否则 retrieve → observe_context，
 * 上下文不足则回到 plan_retrieval 进入下一轮。
 */
export function buildExecutionMermaid(steps: AgentStep[]): string {
    const lines: string[] = ['graph TD']
    lines.push('start([开始]) --> nq[normalize_query] --> rq[route_query]')
    steps.forEach((step, i) => {
        const p = `p${i + 1}`
        const r = `r${i + 1}`
        const o = `o${i + 1}`
        const meta = ACTION_META[step.action]
        const actionLabel = meta ? meta.label : step.action
        lines.push(
            `${p}["Round ${step.round} · ${escapeLabel(actionLabel)}<br/>策略: ${escapeLabel(step.route)}"]`,
        )
        if (i === 0) {
            lines.push(`rq --> ${p}`)
        } else {
            lines.push(`o${i} -->|上下文不足，继续| ${p}`)
        }
        if (step.action === 'refuse') {
            lines.push(`${p} -->|提前拒答| fin([END])`)
            return
        }
        const retrieveParts = [
            step.retrieved_count != null ? `检索 ${step.retrieved_count} 条` : null,
            step.top_score != null ? `Top ${step.top_score}` : null,
        ].filter(Boolean)
        lines.push(
            `${p} --> ${r}["retrieve${retrieveParts.length ? `<br/>${escapeLabel(retrieveParts.join(' · '))}` : ''}"]`,
        )
        const observeLabel =
            step.sufficient == null ? null : step.sufficient ? '上下文充足' : '上下文不足'
        lines.push(
            `${o}["observe_context${observeLabel ? `<br/>${observeLabel}` : ''}"]`,
        )
        if (i === steps.length - 1) {
            const endLabel = step.sufficient ? '上下文充足，生成答案' : '满足停止条件，结束'
            lines.push(`${r} --> ${o} -->|${endLabel}| fin([END])`)
        } else {
            lines.push(`${r} --> ${o}`)
        }
    })
    steps.forEach((step, i) => {
        if (step.action === 'refuse') lines.push(`class p${i + 1} refuseNode`)
        else if (step.sufficient === true) lines.push(`class o${i + 1} okNode`)
        else if (step.sufficient === false) lines.push(`class o${i + 1} warnNode`)
    })
    lines.push(
        'classDef refuseNode fill:#fff1f0,stroke:#ff7875',
        'classDef okNode fill:#f6ffed,stroke:#95de64',
        'classDef warnNode fill:#fffbe6,stroke:#ffd666',
    )
    return lines.join('\n')
}
