import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from './context/AuthContext'
import { UsageGateProvider } from './context/UsageGateContext'
import { ModeProvider } from './context/ModeContext'
import './index.css'
import App from './App.tsx'

const root = document.getElementById('root')
const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            refetchOnWindowFocus: false,
        },
    },
})

if (!root) {
    throw new Error('Root element not found')
}

createRoot(root).render(
    <StrictMode>
        <BrowserRouter>
            <QueryClientProvider client={queryClient}>
                <AuthProvider>
                    <UsageGateProvider>
                        <ModeProvider>
                            <App />
                        </ModeProvider>
                    </UsageGateProvider>
                </AuthProvider>
            </QueryClientProvider>
        </BrowserRouter>
    </StrictMode>
)
