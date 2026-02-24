import { useEffect, useRef } from 'react'
import mermaid from 'mermaid'

interface MermaidDiagramProps {
    code: string
}

mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    themeVariables: {
        primaryColor: '#22c55e',
        primaryTextColor: '#fff',
        primaryBorderColor: '#14b8a6',
        lineColor: '#6b7280',
        secondaryColor: '#1a1a1a',
        tertiaryColor: '#242424',
    },
})

export default function MermaidDiagram({ code }: MermaidDiagramProps) {
    const ref = useRef<HTMLDivElement>(null)

    useEffect(() => {
        if (!ref.current || !code) return
        const render = async () => {
            try {
                const { svg } = await mermaid.render('mermaid-' + Date.now(), code)
                if (ref.current) ref.current.innerHTML = svg
            } catch {
                if (ref.current) ref.current.innerHTML = '<p class="text-red-400">Invalid diagram</p>'
            }
        }
        render()
    }, [code])

    return <div ref={ref} className="bg-dark-800 p-4 rounded-lg overflow-auto" />
}
