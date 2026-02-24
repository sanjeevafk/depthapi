import LZString from 'lz-string'

const CACHE_KEY = 'kb_response_cache_v1'
const MAX_CACHE_SIZE = 50 // Store last 50 responses

interface CachedResponse {
    topic: string
    mode: string
    explanations: Record<string, string>
    timestamp: number
}

export const responseCache = {
    /**
     * Remove cached entries for modes that are no longer supported.
     */
    pruneInvalidModes(allowedModes: string[]) {
        try {
            const compressed = localStorage.getItem(CACHE_KEY)
            if (!compressed) return

            const json = LZString.decompressFromUTF16(compressed)
            if (!json) return

            const cache: CachedResponse[] = JSON.parse(json)
            const pruned = cache.filter(entry => allowedModes.includes(entry.mode))
            if (pruned.length === cache.length) return

            const newCompressed = LZString.compressToUTF16(JSON.stringify(pruned))
            localStorage.setItem(CACHE_KEY, newCompressed)
            console.log(`Pruned cache entries: ${cache.length - pruned.length}`)
        } catch (err) {
            console.warn('Failed to prune cache:', err)
        }
    },
    /**
     * Get cached response for a topic+mode combination
     */
    get(topic: string, mode: string): CachedResponse | null {
        try {
            const compressed = localStorage.getItem(CACHE_KEY)
            if (!compressed) return null

            const json = LZString.decompressFromUTF16(compressed)
            if (!json) return null

            const cache: CachedResponse[] = JSON.parse(json)
            return cache.find(r => r.topic === topic && r.mode === mode) || null
        } catch (err) {
            console.warn('Failed to read cache:', err)
            return null
        }
    },

    /**
     * Store response in cache (compressed)
     */
    set(topic: string, mode: string, explanations: Record<string, string>) {
        try {
            const compressed = localStorage.getItem(CACHE_KEY)
            let cache: CachedResponse[] = []

            if (compressed) {
                const json = LZString.decompressFromUTF16(compressed)
                if (json) cache = JSON.parse(json)
            }

            // Remove existing entry for this topic+mode
            cache = cache.filter(r => !(r.topic === topic && r.mode === mode))

            // Add new entry at the front
            cache.unshift({ topic, mode, explanations, timestamp: Date.now() })

            // Keep only last MAX_CACHE_SIZE entries
            cache = cache.slice(0, MAX_CACHE_SIZE)

            const json = JSON.stringify(cache)
            const newCompressed = LZString.compressToUTF16(json)
            localStorage.setItem(CACHE_KEY, newCompressed)

            console.log(`Cached response for ${topic}:${mode} (${cache.length} total)`)
        } catch (err) {
            console.warn('Failed to cache response:', err)
            // Graceful degradation - if quota exceeded, clear old cache
            if (err instanceof DOMException && err.name === 'QuotaExceededError') {
                console.warn('Cache quota exceeded, clearing old entries')
                this.clear()
            }
        }
    },

    /**
     * Clear all cached responses
     */
    clear() {
        localStorage.removeItem(CACHE_KEY)
        console.log('Response cache cleared')
    },

    /**
     * Get cache stats for debugging
     */
    getStats() {
        try {
            const compressed = localStorage.getItem(CACHE_KEY)
            if (!compressed) return { count: 0, size: 0 }

            const json = LZString.decompressFromUTF16(compressed)
            if (!json) return { count: 0, size: 0 }

            const cache: CachedResponse[] = JSON.parse(json)
            return {
                count: cache.length,
                size: compressed.length,
                uncompressedSize: json.length,
                compressionRatio: (compressed.length / json.length * 100).toFixed(1) + '%'
            }
        } catch {
            return { count: 0, size: 0 }
        }
    }
}
