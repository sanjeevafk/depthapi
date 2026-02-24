import React from 'react';

export const SkeletonLoader: React.FC = () => {
    return (
        <div className="w-full space-y-4 animate-pulse">
            <div className="h-8 bg-dark-700 rounded-md w-3/4 mb-6"></div>
            <div className="space-y-3">
                <div className="h-4 bg-dark-800 rounded w-full"></div>
                <div className="h-4 bg-dark-800 rounded w-full"></div>
                <div className="h-4 bg-dark-800 rounded w-5/6"></div>
            </div>
            <div className="pt-4 space-y-3">
                <div className="h-4 bg-dark-800 rounded w-full"></div>
                <div className="h-4 bg-dark-800 rounded w-4/5"></div>
            </div>
            <div className="pt-6">
                <div className="h-32 bg-dark-800/50 rounded-xl border border-dark-700 w-full mb-4"></div>
                <div className="flex gap-2">
                    <div className="h-10 bg-dark-700 rounded-full w-24"></div>
                    <div className="h-10 bg-dark-700 rounded-full w-24"></div>
                </div>
            </div>
        </div>
    );
};

export const CardSkeleton: React.FC = () => {
    return (
        <div className="bg-dark-800/50 backdrop-blur-sm border border-dark-700 rounded-2xl p-6 shadow-2xl animate-pulse">
            <div className="h-6 bg-dark-700 rounded-md w-1/3 mb-4"></div>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                {[1, 2, 3, 4, 5, 6].map((i) => (
                    <div key={i} className="h-16 bg-dark-700/50 rounded-xl"></div>
                ))}
            </div>
        </div>
    );
};
