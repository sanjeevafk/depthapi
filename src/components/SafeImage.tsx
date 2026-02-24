import { useState } from 'react'

interface SafeImageProps {
    src: string
    alt: string
}

export default function SafeImage({ src, alt }: SafeImageProps) {
    const [error, setError] = useState(false)

    if (error) {
        return (
            <div className="text-xs text-gray-500 italic my-2">
                [Image unavailable: {alt}]
            </div>
        )
    }

    return (
        <div className="my-6 flex flex-col items-center gap-2">
            <img
                src={src}
                alt={alt}
                onError={() => setError(true)}
                className="rounded-xl border border-white/10 max-w-full h-auto shadow-2xl transition-transform hover:scale-[1.02]"
                loading="lazy"
            />
            {alt && alt !== 'Image' && (
                <span className="text-xs text-gray-500 italic">{alt}</span>
            )}
        </div>
    )
}
