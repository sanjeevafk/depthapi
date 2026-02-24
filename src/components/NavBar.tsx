import { LoginButton } from './LoginButton';
import { User, Crown } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useUsageGateContext } from '../context/UsageGateContext';

export const NavBar = () => {
    const { user } = useAuth();
    const { isPro } = useUsageGateContext();

    return (
        <div className="absolute top-0 left-0 w-full p-4 flex justify-end items-start pointer-events-none z-50">
            <div className={`pointer-events-auto flex items-center gap-3 px-1.5 py-1.5 rounded-full transition-all duration-300 ${!user ? '' : 'bg-dark-800/80 backdrop-blur-md border border-dark-700 shadow-lg'}`}>

                {!user ? (
                    <div className="flex items-center gap-3">
                        {/* Guest Label - purely visual indicator */}
                        <div className="hidden md:flex items-center gap-2 px-3 py-1 bg-dark-700/50 rounded-full border border-dark-600">
                            <span className="w-2 h-2 rounded-full bg-gray-500 animate-pulse"></span>
                            <span className="text-gray-400 text-xs font-medium uppercase tracking-wider">Guest</span>
                        </div>

                        {/* High contrast Sign In trigger */}
                        <LoginButton className="!py-2 !px-5 !text-sm !font-semibold shadow-lg hover:shadow-cyan-500/20 hover:scale-105" />
                    </div>
                ) : (
                    <div className="flex items-center gap-3 px-2">
                        {/* Status Icon */}
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center ${isPro
                            ? 'bg-gradient-to-br from-amber-400 to-yellow-600 text-white shadow-amber-500/20'
                            : 'bg-dark-700 text-gray-400'
                            }`}>
                            {isPro ? <Crown size={16} fill="currentColor" /> : <User size={16} />}
                        </div>

                        {/* Text Info */}
                        <div className="flex flex-col pr-2">
                            <span className={`text-xs font-bold leading-none ${isPro ? 'text-amber-400' : 'text-gray-200'}`}>
                                {isPro ? 'Pro Member' : 'Free Account'}
                            </span>
                            <span className="text-[10px] text-gray-500 font-medium leading-tight mt-0.5">
                                {user.email?.split('@')[0]}
                            </span>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
