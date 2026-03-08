import { createContext, useContext, useEffect, useState, useCallback, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Mode } from '../types'

interface ModeContextType {
    mode: Mode
    setMode: (mode: Mode) => void
}

const ModeContext = createContext<ModeContextType | undefined>(undefined)

export function ModeProvider({ children }: { children: ReactNode }): JSX.Element {
    const [searchParams, setSearchParams] = useSearchParams()
    const [mode, setModeState] = useState<Mode>(() => {
        const urlMode = searchParams.get('mode') as Mode
        if (['fast', 'ensemble'].includes(urlMode)) {
            return urlMode
        }
        return 'fast'
    })

    const setMode = useCallback((newMode: Mode) => {
        setModeState(newMode)
        setSearchParams(prev => {
            const next = new URLSearchParams(prev)
            next.set('mode', newMode)
            return next
        }, { replace: true })
    }, [setSearchParams])

    // Sync state with URL changes (e.g. back button)
    useEffect(() => {
        const urlMode = searchParams.get('mode') as Mode
        if (urlMode && ['fast', 'ensemble'].includes(urlMode) && urlMode !== mode) {
            setModeState(urlMode)
        }
    }, [searchParams, mode])

    return (
        <ModeContext.Provider value={{ mode, setMode }}>
            {children}
        </ModeContext.Provider>
    )
}

export function useMode(): ModeContextType {
    const context = useContext(ModeContext)
    if (context === undefined) {
        throw new Error('useMode must be used within a ModeProvider')
    }
    return context
}
