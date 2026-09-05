import { Layout, Menu } from 'antd'
import { DashboardOutlined, FileTextOutlined, MessageOutlined } from '@ant-design/icons'
import { Link, Outlet, useLocation } from 'react-router-dom'
const { Header, Sider, Content } = Layout
const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: <Link to="/">首页</Link> },
  {
    key: '/documents',
    icon: <FileTextOutlined />,
    label: '文档管理',
    disabled: true,
  },
  {
    key: '/chat',
    icon: <MessageOutlined />,
    label: '知识问答',
    disabled: true,
  },
]
export function BasicLayout() {
  const location = useLocation()
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth={64}>
        <div
          style={{
            color: '#fff',
            fontWeight: 600,
            textAlign: 'center',
            padding: '16px 0',
            fontSize: 16,
          }}
        >
          RAG 知识库
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', paddingLeft: 24, fontSize: 16 }}>
          企业级 RAG 知识库
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}