export type ToastType = 'info' | 'success' | 'error'

export const notifyToast = (message: string, type: ToastType = 'info') => {
    if (typeof window === 'undefined') return
    window.dispatchEvent(new CustomEvent('kb-toast', { detail: { type, message } }))
}
