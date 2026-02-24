import type { PinnedTopic } from '../types'

interface PinnedTopicsProps {
    topics: PinnedTopic[]
    onSelect: (topic: string) => void
}

export default function PinnedTopics({ topics, onSelect }: PinnedTopicsProps) {
    return (
        <section className="w-full max-w-4xl mx-auto mt-8">
            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-widest mb-4">Popular Topics</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {topics.map((t) => (
                    <button
                        key={t.id}
                        onClick={() => onSelect(t.title)}
                        className="p-4 bg-dark-800 border border-dark-600 rounded-xl hover:border-cyan-500 hover:bg-dark-700 transition-all text-left group"
                    >
                        <h3 className="font-medium text-white group-hover:text-cyan-400 transition-colors">
                            {t.title}
                        </h3>
                        <p className="text-sm text-gray-500 mt-1 line-clamp-2">{t.description}</p>
                    </button>
                ))}
            </div>
        </section>
    )
}
