import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { User, Session } from '@supabase/supabase-js';
import { supabase } from '../lib/supabase';
import { useChatStore } from '../stores/useChatStore';
import { setMonitoringUser } from '../lib/monitoring';

type UserProfile = {
    is_pro?: boolean;
} & Record<string, unknown>;

interface AuthContextType {
    user: User | null;
    session: Session | null;
    profile: UserProfile | null;
    loading: boolean;
    error: Error | null;
    signInWithGoogle: () => Promise<void>;
    signOut: () => Promise<void>;
    refreshProfile: (options?: { force?: boolean }) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
    const [user, setUser] = useState<User | null>(null);
    const [session, setSession] = useState<Session | null>(null);
    const [profile, setProfile] = useState<UserProfile | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);
    const setIsPro = useChatStore(state => state.setIsPro);
    const supabaseConfigured = Boolean(import.meta.env.VITE_SUPABASE_URL) && Boolean(import.meta.env.VITE_SUPABASE_ANON_KEY)
    const AUTH_TIMEOUT_MS = 3500
    const PROFILE_CACHE_TTL_MS = 30_000
    const profileCacheRef = useRef(new Map<string, { profile: UserProfile | null; fetchedAt: number }>())

    const normalizeError = (err: unknown): Error => {
        return err instanceof Error ? err : new Error('Unknown error')
    }

    const fetchProfile = async (userId: string, options?: { force?: boolean }) => {
        const forceRefresh = options?.force === true
        const profileCache = profileCacheRef.current
        const cached = profileCache.get(userId)
        if (!forceRefresh && cached && Date.now() - cached.fetchedAt <= PROFILE_CACHE_TTL_MS) {
            setProfile(cached.profile)
            return
        }

        try {
            const { data, error } = await supabase
                .from('users')
                .select('*')
                .eq('id', userId)
                .single();

            if (error) {
                console.error('CRITICAL: Error fetching profile:', error);
                setProfile(null);
                profileCache.set(userId, { profile: null, fetchedAt: Date.now() })
            } else {
                setProfile(data);
                profileCache.set(userId, { profile: data, fetchedAt: Date.now() })
            }
        } catch (err) {
            console.error('Failed to fetch profile:', err);
            if (typeof navigator !== 'undefined' && !navigator.onLine) {
                return
            }
            setProfile(null)
        }
    };

    const refreshProfile = async (options?: { force?: boolean }) => {
        if (user) await fetchProfile(user.id, options);
    };

    useEffect(() => {
        setIsPro(profile?.is_pro === true)
    }, [profile, setIsPro])

    useEffect(() => {
        setMonitoringUser(
            user
                ? {
                    id: user.id,
                    email: user.email ?? null,
                }
                : null,
        )
    }, [user])

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
                    void fetchProfile(session.user.id);
                }
            } catch (err) {
                const error = normalizeError(err)
                console.error('Failed to get session:', error);
                setError(error);
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
                void fetchProfile(session.user.id, { force: true });
            } else {
                setProfile(null);
                profileCacheRef.current.clear()
                setIsPro(false)
            }
            setLoading(false);
        });

        return () => {
            mounted = false;
            window.clearTimeout(timeoutId);
            subscription.unsubscribe();
        };
    }, [setIsPro, supabaseConfigured]);

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
        } catch (err) {
            const error = normalizeError(err)
            setError(error);
            console.error('Error signing in with Google:', error);
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
            localStorage.removeItem('guest_usage_count');
            localStorage.removeItem('deep_dive_usage');
            localStorage.removeItem('kb_history_cache');
            profileCacheRef.current.clear()
            setIsPro(false)

            // Redirect to home or reload for clean state
            window.location.href = '/';
        } catch (err) {
            const error = normalizeError(err)
            setError(error);
            console.error('Error signing out:', error);
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
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextType {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
}
