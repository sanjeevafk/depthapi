import { motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { Sparkles, User } from 'lucide-react'

export function LivePreviewCard() {
    const [step, setStep] = useState(0) // 0: idle, 1: typing query, 2: thinking, 3: typing response, 4: done

    // Animation constants
    const queryText = "Explain backpropagation like I know calculus but hate derivations."

    // Structured response with highlights
    const responseLines: { text: string, type: 'normal' | 'highlight' }[][] = [
        [
            { text: "Think of it as the ", type: "normal" },
            { text: "Chain Rule in reverse", type: "highlight" },
            { text: ".", type: "normal" }
        ],
        [
            { text: "We measure the error at the output, then ", type: "normal" },
            { text: "distribute the blame", type: "highlight" },
            { text: " backward through each layer.", type: "normal" }
        ],
        [
            { text: "It’s just ", type: "normal" },
            { text: "recursive correction", type: "highlight" },
            { text: " to minimize the mistake next time.", type: "normal" }
        ]
    ]

    // Cycle through animation
    useEffect(() => {
        let mounted = true

        const runAnimation = async () => {
            while (mounted) {
                // Reset
                setStep(0)
                await new Promise(r => setTimeout(r, 1000))

                // Typing Query
                setStep(1)
                await new Promise(r => setTimeout(r, queryText.length * 30 + 500))

                // Thinking/Processing
                setStep(2)
                await new Promise(r => setTimeout(r, 600))

                // Streaming Response
                setStep(3)
                // distinct lines logic handles the timing, we just wait for it to visually finish
                // Estimate duration: 3 phrases * approx 2s each
                await new Promise(r => setTimeout(r, 7000))

                // Done/Pause
                setStep(4)
                await new Promise(r => setTimeout(r, 3000))
            }
        }

        runAnimation()

        return () => { mounted = false }
    }, [])

    return (
        <div className="w-full">
            <motion.div
                initial={{ opacity: 0, y: 20, rotateX: 5 }}
                animate={{ opacity: 1, y: 0, rotateX: 0 }}
                transition={{ duration: 0.8, ease: "easeOut" }}
                className="relative bg-black/40 backdrop-blur-xl border border-white/10 rounded-3xl p-6 sm:p-8 shadow-2xl overflow-hidden"
            >
                {/* Background Glow */}
                <div className="absolute top-0 right-0 w-64 h-64 bg-cyan-500/5 rounded-full blur-3xl -z-10" />
                <div className="absolute bottom-0 left-0 w-64 h-64 bg-blue-600/5 rounded-full blur-3xl -z-10" />

                {/* Question Section */}
                <div className="flex gap-4 items-start mb-8">
                    <div className="w-10 h-10 rounded-full bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
                        <User className="w-5 h-5 text-gray-400" />
                    </div>
                    <div className="flex-1 pt-2">
                        <TypingText
                            text={queryText}
                            start={step >= 1}
                            className="text-lg sm:text-xl font-medium text-white/90 leading-relaxed"
                        />
                    </div>
                </div>

                {/* Answer Section */}
                <div className="flex gap-4 items-start min-h-[160px]">
                    <div className="w-10 h-10 rounded-full bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center shrink-0">
                        <Sparkles className="w-5 h-5 text-cyan-400" />
                    </div>
                    <div className="flex-1 pt-2 space-y-4">
                        {step >= 2 && (
                            <div className="space-y-4">
                                {step === 2 && (
                                    <motion.div
                                        initial={{ opacity: 0 }}
                                        animate={{ opacity: 1 }}
                                        exit={{ opacity: 0 }}
                                        className="text-cyan-500/50 text-sm font-mono flex items-center gap-2"
                                    >
                                        <span className="w-2 h-2 bg-cyan-500 rounded-full animate-pulse" />
                                        Thinking...
                                    </motion.div>
                                )}

                                {step >= 3 && responseLines.map((lineTokens, i) => (
                                    <ResponseLine
                                        key={i}
                                        tokens={lineTokens}
                                        delay={i * 1.5} // Stagger lines
                                    />
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {/* Status Bar / Decoration */}
                <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-transparent via-cyan-500/20 to-transparent opacity-50" />
            </motion.div>
        </div>
    )
}

function TypingText({ text, start, className }: { text: string, start: boolean, className?: string }) {
    const [displayed, setDisplayed] = useState("")

    useEffect(() => {
        if (!start) {
            setDisplayed("")
            return
        }

        let index = 0
        const interval = setInterval(() => {
            if (index <= text.length) {
                setDisplayed(text.slice(0, index))
                index++
            } else {
                clearInterval(interval)
            }
        }, 30) // Typing speed for query

        return () => clearInterval(interval)
    }, [start, text])

    return (
        <p className={className}>
            {displayed}
            {start && displayed.length < text.length && <span className="animate-pulse text-cyan-500">|</span>}
        </p>
    )
}

function ResponseLine({ tokens, delay }: { tokens: { text: string, type: 'normal' | 'highlight' }[], delay: number }) {
    const [visible, setVisible] = useState(false)

    useEffect(() => {
        const timer = setTimeout(() => setVisible(true), delay * 1000)
        return () => clearTimeout(timer)
    }, [delay])

    if (!visible) return null

    return (
        <motion.p
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="text-gray-300 leading-relaxed text-base sm:text-lg"
        >
            {tokens.map((token, i) => (
                <span
                    key={i}
                    className={token.type === 'highlight' ? "text-cyan-200 font-semibold drop-shadow-[0_0_8px_rgba(34,211,238,0.3)]" : ""}
                >
                    {token.text}
                </span>
            ))}
        </motion.p>
    )
}
