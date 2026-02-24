import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'

mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'loose',
})

interface MermaidProps {
    chart: string
}

export default function Mermaid({ chart }: MermaidProps) {
    const ref = useRef<HTMLDivElement>(null)
    const [hasError, setHasError] = useState(false)
    const [normalizedChart, setNormalizedChart] = useState('')

    useEffect(() => {
        const normalized = chart
            .replace(/^```mermaid\s*/i, '')
            .replace(/```\s*$/i, '')
            .replace(/^mermaid\s*/i, '')
            .trim()
        setNormalizedChart(normalized)
    }, [chart])

    useEffect(() => {
        const renderDiagram = async () => {
            if (!ref.current || !normalizedChart) return

            // Unique ID for each diagram
            const id = 'mermaid-' + Math.random().toString(36).substr(2, 9)

            try {
                const directive = normalizedChart.split('\n')[0]?.trim()
                const allowed = [
                    'graph',
                    'flowchart',
                    'sequenceDiagram',
                    'classDiagram',
                    'stateDiagram',
                    'stateDiagram-v2',
                    'erDiagram',
                    'journey',
                    'gantt',
                    'pie',
                    'quadrantChart',
                    'mindmap',
                    'timeline',
                    'gitGraph',
                    'requirement',
                    'C4Context',
                    'C4Container',
                    'C4Component',
                    'C4Dynamic',
                ]

                if (!allowed.some(prefix => directive.startsWith(prefix))) {
                    setHasError(true)
                    return
                }

                await mermaid.parse(normalizedChart)

                // Clear existing content
                ref.current.innerHTML = ''
                setHasError(false)

                const { svg } = await mermaid.render(id, normalizedChart)
                if (ref.current) {
                    ref.current.innerHTML = svg
                }
            } catch (err) {
                console.error('Mermaid render failure:', err)
                setHasError(true)
            }
        }

        renderDiagram()
    }, [normalizedChart])

    if (hasError) {
        return null
    }

    return (
        <div
            ref={ref}
            data-chart={normalizedChart}
            className="mermaid-container flex justify-center my-8 bg-dark-900/40 backdrop-blur-sm p-6 rounded-2xl border border-white/5 overflow-x-auto shadow-inner"
        />
    )
}
