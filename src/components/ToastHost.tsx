import { useEffect, useState } from 'react'

interface Toast {
    id: string
    message: string
    type?: 'error' | 'success' | 'info'
}

const styleByType: Record<string, string> = {
    error: 'bg-red-500/90 text-white border-red-200/20',
    success: 'bg-emerald-500/90 text-white border-emerald-200/20',
    info: 'bg-dark-700 text-white border-white/10',
}

export default function ToastHost() {
    const [toasts, setToasts] = useState<Toast[]>([])

    useEffect(() => {
        const handler = (event: Event) => {
            const detail = (event as CustomEvent).detail as { type?: Toast['type']; message?: string } | undefined
            if (!detail?.message) return

            const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
                ? crypto.randomUUID()
                : `toast-${Date.now()}`
            const toast: Toast = {
                id,
                message: detail.message,
                type: detail.type || 'info',
            }

            setToasts(prev => [...prev, toast])

            window.setTimeout(() => {
                setToasts(prev => prev.filter(item => item.id !== id))
            }, 3500)
        }

        window.addEventListener('kb-toast', handler)
        return () => window.removeEventListener('kb-toast', handler)
    }, [])

    if (toasts.length === 0) return null

    return (
        <div className="fixed top-4 right-4 z-50 space-y-3">
            {toasts.map(toast => (
                <div
                    key={toast.id}
                    className={`min-w-[220px] max-w-sm rounded-xl border px-4 py-3 text-sm shadow-lg ${styleByType[toast.type || 'info']}`}
                >
                    {toast.message}
                </div>
            ))}
        </div>
    )
}
