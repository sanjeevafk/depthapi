import { exportExplanations } from '../api'
import type { ExportRequest } from '../types'

interface ExportButtonsProps {
    topic: string
    explanations: Record<string, string>
}

export default function ExportButtons({ topic, explanations }: ExportButtonsProps) {
    const handleExport = async (format: 'txt' | 'md') => {
        try {
            const req: ExportRequest = { topic, explanations, format }
            const blob = await exportExplanations(req)
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = `${topic.slice(0, 20)}.${format}`
            a.click()
            URL.revokeObjectURL(url)
        } catch (err) {
            console.error('Export failed:', err)
        }
    }

    return (
        <div className="flex gap-2">
            <button
                onClick={() => handleExport('txt')}
                className="px-4 py-2 bg-dark-600 text-gray-300 rounded-lg hover:bg-dark-500 text-sm transition-colors"
            >
                Export .txt
            </button>
            <button
                onClick={() => handleExport('md')}
                className="px-4 py-2 bg-dark-600 text-gray-300 rounded-lg hover:bg-dark-500 text-sm transition-colors"
            >
                Export .md
            </button>
        </div>
    )
}
