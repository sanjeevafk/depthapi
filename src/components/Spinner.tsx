interface SpinnerProps {
    size?: 'sm' | 'md' | 'lg'
}

export default function Spinner({ size = 'md' }: SpinnerProps) {
    const sizes = { sm: 'w-4 h-4', md: 'w-8 h-8', lg: 'w-12 h-12' }
    return (
        <div className="flex justify-center items-center">
            <div
                className={`${sizes[size]} border-2 border-dark-500 border-t-accent-primary rounded-full animate-spin`}
            />
        </div>
    )
}
