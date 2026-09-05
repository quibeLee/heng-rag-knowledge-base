import { createBrowserRouter } from 'react-router-dom'
import { BasicLayout } from '@/layouts/BasicLayout'
import { HomePage } from '@/pages/HomePage'


export const router = createBrowserRouter([

  {
    path: '/',
    element: <BasicLayout />,
    children: [
      { index: true, element: <HomePage /> },
    ],
  },
])
