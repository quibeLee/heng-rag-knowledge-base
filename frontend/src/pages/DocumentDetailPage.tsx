import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  List,
  Modal,
  Pagination,
  Popconfirm,
  Skeleton,
  Space,
  Statistic,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  RedoOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import {
  deleteDocument,
  getDocument,
  getDocumentChunk,
  listDocumentChunks,
  retryDocument,
} from '@/client/sdk.gen'
import type {
  DocumentChunkDetail,
  DocumentChunkRead,
  DocumentRead,
} from '@/client/types.gen'
import {
  getStatusColor,
  getStatusLabel,
  isTerminalStatus,
} from '@/utils/documentStatus'
import {
  buildDocumentFileUrl,
  canPreviewInline,
  isHtmlMime,
  isMarkdownMime,
  isPdfMime,
} from '@/utils/documentFile'

const { Title, Text, Paragraph } = Typography

const CHUNK_PAGE_SIZE = 20

const DELETABLE_STATUSES: ReadonlySet<DocumentRead['status']> = new Set([
  'ready',
  'failed',
  'uploading',
])

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

function MarkdownPreview({ url }: { url: string }) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setContent(null)
    setError(null)
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.text()
      })
      .then((text) => {
        if (!cancelled) setContent(text)
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [url])

  if (error) return <Alert type="error" message="加载 Markdown 失败" description={error} />
  if (content === null) return <Skeleton active />
  return (
    <div style={{ padding: 16, maxHeight: 600, overflow: 'auto' }}>
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  )
}

export function DocumentDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [chunkPage, setChunkPage] = useState(1)
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null)

  const docQuery = useQuery({
    queryKey: ['documents', 'detail', id],
    queryFn: async () => {
      const res = await getDocument({ path: { document_id: id } })
      return res.data!
    },
    enabled: !!id,
    refetchInterval: (q) => {
      const data = q.state.data
      if (!data) return false
      return isTerminalStatus(data.status) ? false : 3000
    },
  })

  const doc = docQuery.data

  const chunksQuery = useQuery({
    queryKey: ['documents', 'detail', id, 'chunks', chunkPage],
    queryFn: async () => {
      const res = await listDocumentChunks({
        path: { document_id: id },
        query: { page: chunkPage, page_size: CHUNK_PAGE_SIZE },
      })
      return res.data!
    },
    // 仅 ready 时拉取；非 ready 状态下也没有完整 chunks
    enabled: !!id && doc?.status === 'ready',
  })

  const chunkDetailQuery = useQuery({
    queryKey: ['documents', 'detail', id, 'chunk', activeChunkId],
    queryFn: async () => {
      const res = await getDocumentChunk({
        path: { document_id: id, chunk_id: activeChunkId! },
      })
      return res.data!
    },
    enabled: !!activeChunkId,
  })

  const retryMutation = useMutation({
    mutationFn: async () => {
      const res = await retryDocument({ path: { document_id: id } })
      return res.data!
    },
    onSuccess: () => {
      message.success('已重新提交解析')
      queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async () => {
      await deleteDocument({ path: { document_id: id } })
    },
    onSuccess: () => {
      message.success('文档已删除')
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      navigate('/documents')
    },
  })

  const previewUrl = useMemo(() => buildDocumentFileUrl(id, { download: false }), [id])
  const downloadUrl = useMemo(() => buildDocumentFileUrl(id, { download: true }), [id])

  if (docQuery.isLoading) return <Skeleton active />
  if (!doc) return null

  const canDelete = DELETABLE_STATUSES.has(doc.status)
  const canRetry = doc.status === 'failed'
  const supportsInlinePreview = canPreviewInline(doc.mime_type)

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Link to="/documents">
          <Button icon={<ArrowLeftOutlined />}>返回文档列表</Button>
        </Link>
        <Button
          icon={<DownloadOutlined />}
          href={downloadUrl}
          target="_blank"
          rel="noreferrer"
        >
          下载
        </Button>
        {supportsInlinePreview ? (
          <Button
            icon={<EyeOutlined />}
            href={previewUrl}
            target="_blank"
            rel="noreferrer"
          >
            新窗口打开
          </Button>
        ) : null}
        {canRetry ? (
          <Button
            icon={<RedoOutlined />}
            loading={retryMutation.isPending}
            onClick={() => retryMutation.mutate()}
          >
            重试解析
          </Button>
        ) : null}
        <Popconfirm
          title="确认删除该文档？"
          description="将同时删除文档内容、所有切片以及云端原文件，无法恢复。"
          okText="删除"
          okButtonProps={{ danger: true }}
          cancelText="取消"
          disabled={!canDelete}
          onConfirm={() => deleteMutation.mutate()}
        >
          <Button
            danger
            icon={<DeleteOutlined />}
            disabled={!canDelete}
            loading={deleteMutation.isPending}
          >
            删除
          </Button>
        </Popconfirm>
      </Space>

      <Title level={3} style={{ marginBottom: 16 }}>
        {doc.name}
      </Title>

      {doc.status === 'failed' && doc.error_message ? (
        <Alert
          type="error"
          message="入库失败"
          description={doc.error_message}
          showIcon
          style={{ marginBottom: 16 }}
        />
      ) : null}

      <Descriptions bordered column={1} size="middle" style={{ marginBottom: 24 }}>
        <Descriptions.Item label="状态">
          <Tag color={getStatusColor(doc.status)}>{getStatusLabel(doc.status)}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="ID">{doc.id}</Descriptions.Item>
        <Descriptions.Item label="文件 hash">{doc.file_hash}</Descriptions.Item>
        <Descriptions.Item label="MIME 类型">{doc.mime_type}</Descriptions.Item>
        <Descriptions.Item label="大小">{formatSize(doc.size)}</Descriptions.Item>
        <Descriptions.Item label="上传时间">
          {new Date(doc.created_at).toLocaleString('zh-CN')}
        </Descriptions.Item>
        <Descriptions.Item label="更新时间">
          {new Date(doc.updated_at).toLocaleString('zh-CN')}
        </Descriptions.Item>
      </Descriptions>

      <Card title="原文预览" style={{ marginBottom: 24 }}>
        <PreviewArea mimeType={doc.mime_type} previewUrl={previewUrl} />
      </Card>

      <Card title="切分结果">
        <ChunksSection
          docStatus={doc.status}
          chunksQuery={chunksQuery}
          page={chunkPage}
          onPageChange={setChunkPage}
          onPickChunk={setActiveChunkId}
        />
      </Card>

      <Modal
        title="Chunk 完整内容"
        open={!!activeChunkId}
        onCancel={() => setActiveChunkId(null)}
        footer={null}
        width={720}
      >
        {chunkDetailQuery.isLoading ? (
          <Skeleton active />
        ) : chunkDetailQuery.data ? (
          <ChunkDetailBody chunk={chunkDetailQuery.data} />
        ) : null}
      </Modal>
    </div>
  )
}

function PreviewArea({ mimeType, previewUrl }: { mimeType: string; previewUrl: string }) {
  if (isPdfMime(mimeType) || isHtmlMime(mimeType)) {
    return (
      <iframe
        title="document-preview"
        src={previewUrl}
        style={{ width: '100%', height: 600, border: '1px solid #f0f0f0' }}
      />
    )
  }
  if (isMarkdownMime(mimeType)) {
    return <MarkdownPreview url={previewUrl} />
  }
  return (
    <Alert
      type="info"
      showIcon
      message="该格式不支持内联预览"
      description="DOCX 等富文本格式请下载后用本地编辑器查看。"
    />
  )
}

interface ChunksQueryResult {
  isLoading: boolean
  data:
    | {
        items: DocumentChunkRead[]
        total: number
        stats?:
          | {
              total: number
              avg_length: number
              min_length: number
              max_length: number
            }
          | null
      }
    | undefined
}

function ChunksSection({
  docStatus,
  chunksQuery,
  page,
  onPageChange,
  onPickChunk,
}: {
  docStatus: DocumentRead['status']
  chunksQuery: ChunksQueryResult
  page: number
  onPageChange: (p: number) => void
  onPickChunk: (id: string) => void
}) {
  if (docStatus !== 'ready') {
    return <Empty description="等待入库完成后展示切分结果" />
  }
  if (chunksQuery.isLoading || !chunksQuery.data) {
    return <Skeleton active />
  }
  const { items, total, stats } = chunksQuery.data
  if (total === 0) {
    return <Empty description="没有 chunk" />
  }
  return (
    <>
      {stats ? (
        <Space size="large" style={{ marginBottom: 16 }} wrap>
          <Statistic title="切片总数" value={stats.total} />
          <Statistic title="平均字符数" value={stats.avg_length} />
          <Statistic title="最短" value={stats.min_length} />
          <Statistic title="最长" value={stats.max_length} />
        </Space>
      ) : null}
      <List<DocumentChunkRead>
        dataSource={items}
        bordered
        renderItem={(chunk) => (
          <List.Item
            style={{ cursor: 'pointer' }}
            onClick={() => onPickChunk(chunk.id)}
          >
            <List.Item.Meta
              title={
                <Space wrap>
                  <Text strong>#{chunk.chunk_index}</Text>
                  {chunk.page_no != null ? (
                    <Tag color="blue">第 {chunk.page_no} 页</Tag>
                  ) : null}
                  {chunk.section_path ? (
                    <Text type="secondary" ellipsis style={{ maxWidth: 320 }}>
                      {chunk.section_path}
                    </Text>
                  ) : null}
                  <Tag>字符数 {chunk.char_count}</Tag>
                  <Text type="secondary" code>
                    {chunk.chunk_hash.slice(0, 8)}
                  </Text>
                </Space>
              }
              description={
                <Paragraph
                  type="secondary"
                  style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
                >
                  {chunk.content_excerpt}
                </Paragraph>
              }
            />
          </List.Item>
        )}
      />
      <Pagination
        style={{ marginTop: 16, textAlign: 'right' }}
        align="end"
        current={page}
        pageSize={CHUNK_PAGE_SIZE}
        total={total}
        showSizeChanger={false}
        onChange={onPageChange}
      />
    </>
  )
}

function ChunkDetailBody({ chunk }: { chunk: DocumentChunkDetail }) {
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Text strong>#{chunk.chunk_index}</Text>
        {chunk.page_no != null ? <Tag color="blue">第 {chunk.page_no} 页</Tag> : null}
        <Tag>字符数 {chunk.char_count}</Tag>
        <Text type="secondary" code>
          {chunk.chunk_hash}
        </Text>
      </Space>
      {chunk.section_path ? (
        <Paragraph type="secondary">{chunk.section_path}</Paragraph>
      ) : null}
      <pre
        style={{
          background: '#fafafa',
          border: '1px solid #f0f0f0',
          padding: 12,
          maxHeight: 480,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {chunk.content}
      </pre>
    </>
  )
}