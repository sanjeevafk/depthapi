import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle, Loader2 } from 'lucide-react';
import { waitForPaymentConfirmation } from '../lib/payments';
import { useAuth } from '../context/AuthContext';

export default function SuccessPage() {
    const navigate = useNavigate();
    const { refreshProfile } = useAuth();
    const [status, setStatus] = useState<'verifying' | 'success' | 'error'>('verifying');

    useEffect(() => {
        const verifyPayment = async () => {
            try {
                // Wait for webhook to process and upgrade user
                const isPro = await waitForPaymentConfirmation();

                if (isPro) {
                    setStatus('success');
                    // Refresh user profile to get updated Pro status
                    await refreshProfile();

                    // Redirect to app after 3 seconds
                    setTimeout(() => {
                        navigate('/app');
                    }, 3000);
                } else {
                    setStatus('error');
                }
            } catch (error) {
                console.error('Payment verification error:', error);
                setStatus('error');
            }
        };

        verifyPayment();
    }, [navigate, refreshProfile]);

    return (
        <div className="min-h-screen bg-gradient-to-br from-gray-900 via-gray-800 to-black flex items-center justify-center p-4">
            <div className="max-w-md w-full bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl p-8">
                {status === 'verifying' && (
                    <div className="text-center">
                        <div className="w-20 h-20 mx-auto mb-6 bg-blue-500/20 rounded-full flex items-center justify-center">
                            <Loader2 className="w-10 h-10 text-blue-400 animate-spin" />
                        </div>
                        <h1 className="text-2xl font-bold text-white mb-2">
                            Verifying Payment
                        </h1>
                        <p className="text-gray-400">
                            Please wait while we confirm your upgrade...
                        </p>
                    </div>
                )}

                {status === 'success' && (
                    <div className="text-center">
                        <div className="w-20 h-20 mx-auto mb-6 bg-green-500/20 rounded-full flex items-center justify-center">
                            <CheckCircle className="w-10 h-10 text-green-400" />
                        </div>
                        <h1 className="text-2xl font-bold text-white mb-2">
                            Welcome to Pro! 🎉
                        </h1>
                        <p className="text-gray-400 mb-6">
                            Your account has been upgraded successfully. You now have access to all premium features!
                        </p>
                        <div className="bg-gray-800/50 rounded-lg p-4 mb-6">
                            <h3 className="text-sm font-semibold text-gray-300 mb-2">
                                What's unlocked:
                            </h3>
                            <ul className="text-sm text-gray-400 space-y-1 text-left">
                                <li>✓ Unlimited AI explanations</li>
                                <li>✓ Access to all premium modes</li>
                                <li>✓ Markdown & text exports</li>
                                <li>✓ Priority support</li>
                            </ul>
                        </div>
                        <p className="text-sm text-gray-500">
                            Redirecting you to the app...
                        </p>
                    </div>
                )}

                {status === 'error' && (
                    <div className="text-center">
                        <div className="w-20 h-20 mx-auto mb-6 bg-red-500/20 rounded-full flex items-center justify-center">
                            <span className="text-4xl">⚠️</span>
                        </div>
                        <h1 className="text-2xl font-bold text-white mb-2">
                            Verification Taking Longer
                        </h1>
                        <p className="text-gray-400 mb-6">
                            We're still processing your payment. This can take a few minutes.
                            Please check back shortly or contact support if the issue persists.
                        </p>
                        <button
                            onClick={() => navigate('/app')}
                            className="w-full py-3 bg-white text-gray-900 rounded-lg font-semibold hover:bg-gray-100 transition-colors"
                        >
                            Return to App
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
