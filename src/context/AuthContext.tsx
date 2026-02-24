import React, { createContext, useContext, useEffect, useState } from 'react';
import { User, Session, AuthError } from '@supabase/supabase-js';
import { supabase } from '../lib/supabase';

interface AuthContextType {
    user: User | null;
    session: Session | null;
    profile: any | null;
    loading: boolean;
    error: AuthError | null;
    signInWithGoogle: () => Promise<void>;
    signOut: () => Promise<void>;
    refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [user, setUser] = useState<User | null>(null);
    const [session, setSession] = useState<Session | null>(null);
    const [profile, setProfile] = useState<any | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<AuthError | null>(null);
    const supabaseConfigured = Boolean(import.meta.env.VITE_SUPABASE_URL) && Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY)
    const AUTH_TIMEOUT_MS = 3500

    const fetchProfile = async (userId: string) => {
        try {
            const { data, error } = await supabase
                .from('users')
                .select('*')
                .eq('id', userId)
                .single();

            if (error) {
                console.error('CRITICAL: Error fetching profile:', error);
                // If profile doesn't exist yet, it's okay, backend will create it
                setProfile(null);
            } else {
                setProfile(data);
                if (data?.is_pro) {
                    localStorage.setItem('knowbear_pro_status', 'true');
                } else {
                    localStorage.removeItem('knowbear_pro_status');
                }
            }
        } catch (err) {
            console.error('Failed to fetch profile:', err);
        }
    };

    const refreshProfile = async () => {
        if (user) await fetchProfile(user.id);
    };

    useEffect(() => {
        if (!supabaseConfigured) {
            console.warn('Supabase env missing. Skipping auth initialization.');
            setLoading(false);
            return;
        }

        let mounted = true;
        const timeoutId = window.setTimeout(() => {
            if (mounted) {
                console.warn('Auth initialization timed out. Rendering app without auth.');
                setLoading(false);
            }
        }, AUTH_TIMEOUT_MS);

        const init = async () => {
            try {
                const { data: { session }, error } = await supabase.auth.getSession();
                if (error) {
                    console.error('Error getting session:', error);
                    setError(error);
                }
                setSession(session);
                setUser(session?.user ?? null);
                if (session?.user) {
                    fetchProfile(session.user.id);
                }
            } catch (err: any) {
                console.error('Failed to get session:', err);
                setError(err as AuthError);
            } finally {
                if (mounted) {
                    setLoading(false);
                    window.clearTimeout(timeoutId);
                }
            }
        };

        init();

        const {
            data: { subscription },
        } = supabase.auth.onAuthStateChange((_event, session) => {
            setSession(session);
            setUser(session?.user ?? null);
            if (session?.user) {
                fetchProfile(session.user.id);
            } else {
                setProfile(null);
                localStorage.removeItem('knowbear_pro_status');
            }
            setLoading(false);
        });

        return () => {
            mounted = false;
            window.clearTimeout(timeoutId);
            subscription.unsubscribe();
        };
    }, [supabaseConfigured]);

    const signInWithGoogle = async () => {
        try {
            if (!supabaseConfigured) {
                throw new Error('Supabase is not configured');
            }
            const { error } = await supabase.auth.signInWithOAuth({
                provider: 'google',
                options: {
                    redirectTo: window.location.origin,
                },
            });
            if (error) throw error;
        } catch (err: any) {
            setError(err);
            console.error('Error signing in with Google:', err);
        }
    };

    const signOut = async () => {
        try {
            if (!supabaseConfigured) {
                setUser(null);
                setSession(null);
                return;
            }
            await supabase.auth.signOut();
            // Clear local storage items that should reset on logout
            localStorage.removeItem('knowbear_pro_status');
            localStorage.removeItem('guest_usage_count');
            localStorage.removeItem('deep_dive_usage');
            localStorage.removeItem('kb_history_cache');

            // Redirect to home or reload for clean state
            window.location.href = '/';
        } catch (err: any) {
            setError(err);
            console.error('Error signing out:', err);
        }
    };

    return (
        <AuthContext.Provider value={{ user, session, profile, loading, error, signInWithGoogle, signOut, refreshProfile }}>
            {loading ? (
                <div className="flex items-center justify-center min-h-screen bg-gray-900 text-white">
                    <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-primary-500"></div>
                </div>
            ) : (
                children
            )}
        </AuthContext.Provider>
    );
};

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};
