import { Component, ReactNode } from 'react'
import { AlertTriangle, RefreshCcw, Home } from 'lucide-react'

interface Props {
    children: ReactNode
}

interface State {
    hasError: boolean
    error: Error | null
    errorInfo: any
}

export class ErrorBoundary extends Component<Props, State> {
    constructor(props: Props) {
        super(props)
        this.state = {
            hasError: false,
            error: null,
            errorInfo: null
        }
    }

    static getDerivedStateFromError(error: Error): State {
        return {
            hasError: true,
            error,
            errorInfo: null
        }
    }

    componentDidCatch(error: Error, errorInfo: any) {
        console.error('ErrorBoundary caught an error:', error, errorInfo)
        this.setState({
            error,
            errorInfo
        })
    }

    handleReload = () => {
        window.location.reload()
    }

    handleGoHome = () => {
        window.location.href = '/'
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="min-h-screen bg-gradient-to-b from-black via-dark-900 to-black flex items-center justify-center p-4">
                    <div className="max-w-2xl w-full bg-dark-800/50 backdrop-blur-xl border border-red-500/20 rounded-2xl p-8 shadow-2xl">
                        <div className="flex items-center gap-4 mb-6">
                            <div className="p-4 bg-red-500/10 rounded-full">
                                <AlertTriangle className="w-8 h-8 text-red-400" />
                            </div>
                            <div>
                                <h1 className="text-2xl font-bold text-white mb-1">Something went wrong</h1>
                                <p className="text-gray-400">We encountered an unexpected error</p>
                            </div>
                        </div>

                        {this.state.error && (
                            <div className="bg-dark-900/50 border border-white/5 rounded-xl p-4 mb-6">
                                <p className="text-sm text-red-300 font-mono mb-2">
                                    {this.state.error.toString()}
                                </p>
                                {process.env.NODE_ENV === 'development' && this.state.errorInfo && (
                                    <details className="mt-3">
                                        <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-400">
                                            Stack trace
                                        </summary>
                                        <pre className="mt-2 text-xs text-gray-600 overflow-auto max-h-60">
                                            {this.state.errorInfo.componentStack}
                                        </pre>
                                    </details>
                                )}
                            </div>
                        )}

                        <div className="flex gap-3">
                            <button
                                onClick={this.handleReload}
                                className="flex-1 flex items-center justify-center gap-2 px-6 py-3 bg-cyan-600 hover:bg-cyan-500 text-white rounded-xl transition-all font-medium"
                            >
                                <RefreshCcw className="w-4 h-4" />
                                Reload Page
                            </button>
                            <button
                                onClick={this.handleGoHome}
                                className="flex-1 flex items-center justify-center gap-2 px-6 py-3 bg-dark-700 hover:bg-dark-600 border border-white/10 text-white rounded-xl transition-all font-medium"
                            >
                                <Home className="w-4 h-4" />
                                Go Home
                            </button>
                        </div>

                        <p className="text-xs text-gray-500 text-center mt-6">
                            If this problem persists, please contact support
                        </p>
                    </div>
                </div>
            )
        }

        return this.props.children
    }
}
