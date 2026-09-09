import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'
import { router } from '@/routes'
import 'antd/dist/reset.css'
import '@/api/client'
import { useAuthStore } from '@/stores/authStore'

// 应用挂载前先从 localStorage 恢复登录态
useAuthStore.getState().hydrate()
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
})
const root = document.getElementById('root')
if (!root) throw new Error('root element not found')
createRoot(root).render(
  <StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1677ff' } }}>
        <AntdApp>
            <QueryClientProvider client={queryClient}>
                <RouterProvider router={router} />
            </QueryClientProvider>
        </AntdApp>
    </ConfigProvider>
  </StrictMode>,
)