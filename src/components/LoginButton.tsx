import React from 'react';
import { useAuth } from '../context/AuthContext';

export const LoginButton: React.FC<{ className?: string }> = ({ className = '' }) => {
    const { signInWithGoogle, user, loading } = useAuth();

    if (loading) return null;
    if (user) return null;

    return (
        <button
            onClick={signInWithGoogle}
            className={`group flex items-center justify-center gap-2 bg-white hover:bg-gray-50 text-gray-700 rounded-full transition-all duration-300 border border-gray-200 hover:border-gray-300 font-medium ${className}`}
            aria-label="Sign in with Google"
        >
            <div className="flex-shrink-0">
                <svg viewBox="0 0 24 24" width="20" height="20" xmlns="http://www.w3.org/2000/svg">
                    <g transform="matrix(1, 0, 0, 1, 27.009001, -39.238998)">
                        <path fill="#4285F4" d="M -3.264 51.509 C -3.264 50.719 -3.334 49.969 -3.454 49.239 L -14.754 49.239 L -14.754 53.749 L -8.284 53.749 C -8.574 55.225 -9.422 56.474 -10.686 57.324 L -10.686 60.325 L -6.842 60.325 C -4.604 58.219 -3.264 55.109 -3.264 51.509 Z" />
                        <path fill="#34A853" d="M -14.754 63.239 C -11.516 63.239 -8.801 62.157 -6.842 60.349 L -10.686 57.348 C -11.761 58.068 -13.139 58.489 -14.754 58.489 C -17.885 58.489 -20.533 56.374 -21.481 53.524 L -25.392 53.524 L -25.392 56.558 C -23.452 60.414 -19.463 63.239 -14.754 63.239 Z" />
                        <path fill="#FBBC05" d="M -21.481 53.524 C -21.731 52.774 -21.873 51.974 -21.873 51.149 C -21.873 50.324 -21.725 49.524 -21.481 48.774 L -21.481 45.741 L -25.392 45.741 C -26.191 47.341 -26.652 49.191 -26.652 51.149 C -26.652 53.107 -26.191 54.957 -25.392 56.558 L -21.481 53.524 Z" />
                        <path fill="#EA4335" d="M -14.754 43.809 C -12.982 43.809 -11.405 44.409 -10.153 45.609 L -6.742 42.199 C -8.795 40.284 -11.513 39.059 -14.754 39.059 C -19.463 39.059 -23.452 41.884 -25.392 45.741 L -21.481 48.774 C -20.533 45.924 -17.885 43.809 -14.754 43.809 Z" />
                    </g>
                </svg>
            </div>
            <span className="tracking-wide">Sign in with Google</span>
        </button>
    );
};
