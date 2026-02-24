import { Suspense, lazy } from 'react'
import { Routes, Route } from 'react-router-dom'
import { ErrorBoundary } from './components/ErrorBoundary'
import ToastHost from './components/ToastHost'

const LandingPage = lazy(() => import('./pages/LandingPage'))
const AppPage = lazy(() => import('./pages/AppPage'))
const SuccessPage = lazy(() => import('./pages/SuccessPage'))

export default function App() {
    return (
        <ErrorBoundary>
            <ToastHost />
            <Suspense fallback={<div className="min-h-screen bg-black text-white flex items-center justify-center">Loading...</div>}>
                <Routes>
                    <Route path="/" element={<LandingPage />} />
                    <Route path="/app" element={<AppPage />} />
                    <Route path="/success" element={<SuccessPage />} />
                </Routes>
            </Suspense>
        </ErrorBoundary>
    )
}
