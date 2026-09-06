import {createBrowserRouter} from 'react-router-dom'
import {BasicLayout} from '@/layouts/BasicLayout'
import {HomePage} from '@/pages/HomePage'
import {DocumentsPage} from '@/pages/DocumentsPage'
import {DocumentDetailPage} from '@/pages/DocumentDetailPage'
import {ChatPage} from "@/pages/ChatPage.tsx";

export const router = createBrowserRouter([
    {
        path: '/',
        element: <BasicLayout/>,

        children: [
            {index: true, element: <HomePage/>},
            {path: 'documents', element: <DocumentsPage/>},
            {path: 'documents/:id', element: <DocumentDetailPage/>},
            {path: 'chat', element: <ChatPage/>},
        ],
    },
])
