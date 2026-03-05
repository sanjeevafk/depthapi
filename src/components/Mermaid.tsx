import { Suspense, lazy } from 'react'
import type { MermaidProps } from './MermaidRenderer'

const MermaidRenderer = lazy(() => import('./MermaidRenderer'))

export type { MermaidProps }

export default function Mermaid(props: MermaidProps) {
    return (
        <Suspense
            fallback={
                <div className="my-8 rounded-2xl border border-white/5 bg-dark-900/40 p-6 text-xs text-gray-500">
                    Rendering diagram...
                </div>
            }
        >
            <MermaidRenderer {...props} />
        </Suspense>
    )
}
