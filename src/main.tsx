import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { UsageGateProvider } from './context/UsageGateContext'
import { ModeProvider } from './context/ModeContext'
import './index.css'
import App from './App.tsx'

const root = document.getElementById('root')

if (!root) {
    throw new Error('Root element not found')
}

createRoot(root).render(
    <StrictMode>
        <BrowserRouter>
            <AuthProvider>
                <UsageGateProvider>
                    <ModeProvider>
                        <App />
                    </ModeProvider>
                </UsageGateProvider>
            </AuthProvider>
        </BrowserRouter>
    </StrictMode>
)
