interface RazorpayOptions {
    key: string;
    amount: number;
    currency: string;
    name: string;
    description: string;
    handler: (response: any) => void;
    prefill?: {
        name?: string;
        email?: string;
        contact?: string;
    };
    theme?: {
        color?: string;
    };
}

declare global {
    interface Window {
        Razorpay: new (options: RazorpayOptions) => any;
    }
}

export const loadRazorpay = (): Promise<boolean> => {
    return new Promise((resolve) => {
        if (window.Razorpay) {
            resolve(true);
            return;
        }
        const script = document.createElement('script');
        script.src = 'https://checkout.razorpay.com/v1/checkout.js';
        script.onload = () => resolve(true);
        script.onerror = () => resolve(false);
        document.body.appendChild(script);
    });
};

export const openCheckout = async (
    onSuccess: () => void,
    onError: (error: any) => void
) => {
    // MOCK FLOW IF KEYS ARE MISSING
    // Since we don't have keys in this environment yet, we simulate the flow
    const MOCK_MODE = true;

    if (MOCK_MODE) {
        console.log('Mocking Razorpay Checkout...');
        setTimeout(() => {
            const confirm = window.confirm('Simulate successful payment?');
            if (confirm) {
                onSuccess();
            } else {
                onError('Payment cancelled by user');
            }
        }, 1000);
        return;
    }

    /* 
    // REAL IMPLEMENTATION TO BE ENABLED WHEN KEYS ARE PRESENT
    const res = await loadRazorpay();
    if (!res) {
        onError('Razorpay SDK failed to load');
        return;
    }

    const options: RazorpayOptions = {
        key: import.meta.env.VITE_RAZORPAY_KEY_ID || '', // Needs to be added to .env
        amount: 49900, // 499.00 INR
        currency: 'INR',
        name: 'KnowBear Pro',
        description: 'Monthly Subscription',
        handler: function (response: any) {
            // Verify payment on backend here usually
            onSuccess();
        },
        prefill: {
            name: 'KnowBear User',
            email: 'user@example.com',
        },
        theme: {
            color: '#06b6d4', // Cyan-500
        },
    };

    const rzp1 = new window.Razorpay(options);
    rzp1.open(); 
    */
};
