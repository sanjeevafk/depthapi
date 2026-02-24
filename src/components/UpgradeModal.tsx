import { useState } from 'react';
import { X, Lock, Star } from 'lucide-react';
import { LoginButton } from './LoginButton';
import { createCheckoutSession } from '../lib/payments';
import { useUsageGate } from '../hooks/useUsageGate';
import { useAuth } from '../context/AuthContext';

interface UpgradeModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export const UpgradeModal: React.FC<UpgradeModalProps> = ({ isOpen, onClose }) => {
    const [loading, setLoading] = useState(false);

    const handleUpgrade = async () => {
        setLoading(true);
        try {
            // Redirect to Dodo Payments checkout
            await createCheckoutSession(
                (err: any) => {
                    setLoading(false);
                    console.error(err);
                    alert('Failed to initiate checkout. Please try again.');
                }
            );
        } catch (error) {
            setLoading(false);
            console.error('Checkout error:', error);
            alert('Failed to initiate checkout. Please try again.');
        }
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
            <div className="bg-gray-900 border border-gray-800 rounded-xl shadow-2xl max-w-md w-full p-6 relative overflow-hidden">
                {/* Glow effect */}
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-500 to-purple-600"></div>

                <button
                    onClick={onClose}
                    className="absolute top-4 right-4 text-gray-400 hover:text-white transition-colors"
                >
                    <X size={20} />
                </button>

                <UpgradeContent onUpgrade={handleUpgrade} loading={loading} />
            </div>
        </div>
    );
};

const UpgradeContent: React.FC<{ onUpgrade: () => void, loading: boolean }> = ({ onUpgrade, loading }) => {
    const { user } = useAuth();
    const { paywallContext } = useUsageGate();

    const getMessage = () => {
        if (!user) {
            return {
                title: "Guest Limit Reached",
                body: "You've reached the free usage limit for guest users. Sign in to continue utilizing KnowBear without interruptions.",
                icon: <Lock size={32} />
            };
        }

        // Contextual messaging
        if (paywallContext?.mode === 'ensemble' && paywallContext?.action === 'export_data') {
            return {
                title: "Unlock Export in Ensemble Mode",
                body: "Export your ensemble results as Markdown or text. Upgrade to Pro for unlimited exports and advanced features.",
                icon: <Star size={32} fill="currentColor" />
            };
        }

        if (paywallContext?.action === 'premium_mode') {
            const modeName = paywallContext.mode?.replace('_', ' ') || 'Premium';
            return {
                title: `Unlock ${modeName.charAt(0).toUpperCase() + modeName.slice(1)} Mode`,
                body: `Get better, deeper answers with our high-reasoning models. Upgrade to Pro for unlimited access to all modes.`,
                icon: <Star size={32} fill="currentColor" />
            };
        }

        return {
            title: "Unlock Premium Features",
            body: "Exporting is available to Pro users. Upgrade now to unlock Markdown/text exports and unlimited usage.",
            icon: <Star size={32} fill="currentColor" />
        };
    };

    const { title, body, icon } = getMessage();

    return (
        <div className="flex flex-col items-center text-center mt-2">
            <div className={`w-16 h-16 rounded-full flex items-center justify-center mb-4 text-white shadow-lg ${!user ? 'bg-gray-800 text-blue-400' : 'bg-gradient-to-br from-yellow-400 to-orange-600 shadow-orange-500/30'}`}>
                {icon}
            </div>

            <h2 className="text-2xl font-bold text-white mb-2">{title}</h2>
            <p className="text-gray-400 mb-6">{body}</p>

            <div className="w-full">
                {!user ? (
                    <LoginButton className="w-full justify-center py-3 text-lg" />
                ) : (
                    <button
                        onClick={onUpgrade}
                        disabled={loading}
                        className="w-full flex items-center justify-center gap-2 py-3 text-lg font-bold bg-white text-gray-900 rounded-lg hover:bg-gray-100 transition-all disabled:opacity-50"
                    >
                        {loading ? 'Processing...' : 'Upgrade to Pro - ₹200/mo'}
                    </button>
                )}
            </div>

            <p className="mt-4 text-xs text-gray-500">
                {!user ? 'By signing in, you agree to our Terms of Service.' : 'Secure payment via Dodo Payments. Cancel anytime.'}
            </p>
        </div>
    );
};
