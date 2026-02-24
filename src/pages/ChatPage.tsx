import ConversationList from '../components/chat/ConversationList'
import MessageList from '../components/chat/MessageList'
import ChatInput from '../components/chat/ChatInput'
import { useMessages } from '../hooks/useMessages'
import { useAuth } from '../context/AuthContext'

export default function ChatPage() {
    const { user, signInWithGoogle } = useAuth()

    useMessages()

    if (!user) {
        return (
            <div className="min-h-screen bg-dark-900 text-white flex items-center justify-center">
                <div className="bg-dark-800 border border-white/5 rounded-2xl p-8 max-w-md text-center">
                    <h1 className="text-2xl font-semibold mb-3">Sign in to start chatting</h1>
                    <p className="text-sm text-gray-400 mb-6">
                        Your conversations are stored securely in Supabase and synced across sessions.
                    </p>
                    <button
                        onClick={() => void signInWithGoogle()}
                        className="w-full rounded-xl bg-white text-black py-3 text-sm font-semibold hover:bg-gray-100 transition"
                    >
                        Continue with Google
                    </button>
                </div>
            </div>
        )
    }

    return (
        <div className="flex flex-col md:flex-row h-screen bg-dark-900 text-white">
            <ConversationList />
            <div className="flex-1 flex flex-col min-h-0">
                <MessageList />
                <ChatInput />
            </div>
        </div>
    )
}
