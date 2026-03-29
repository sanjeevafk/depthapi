"""Legal policy endpoints."""

from fastapi import APIRouter

router = APIRouter(tags=["legal"])

TERMS_OF_SERVICE = {
    "title": "Terms of Service",
    "last_updated": "2026-03-29",
    "sections": [
        {
            "title": "Account Usage",
            "body": (
                "You must sign in with a Google account, keep your information accurate, "
                "and are responsible for all activity under your account. "
                "Do not misuse the Service or interfere with other users."
            ),
        },
        {
            "title": "Subscriptions & Cancellations",
            "body": (
                "Paid plans renew monthly unless canceled before the next billing date. "
                "You can cancel from your account settings, and access continues through the "
                "end of the current billing period."
            ),
        },
        {
            "title": "Payments via Dodo",
            "body": (
                "Payments are processed by Dodo Payments. We do not store full payment card details. "
                "By subscribing, you authorize Dodo to charge your selected payment method according "
                "to your plan."
            ),
        },
        {
            "title": "Intellectual Property",
            "body": (
                "KnowBear and its licensors own the Service, including software, branding, "
                "and content. You retain ownership of content you submit, and grant KnowBear "
                "a limited license to host and display it to provide the Service."
            ),
        },
        {
            "title": "Limitation of Liability",
            "body": (
                "To the maximum extent permitted by law, KnowBear is not liable for indirect, "
                "incidental, special, or consequential damages, or for loss of profits, data, "
                "or goodwill arising from use of the Service."
            ),
        },
        {
            "title": "Governing Law",
            "body": (
                "These Terms are governed by the laws of the jurisdiction in which KnowBear is "
                "established, without regard to conflict of law rules."
            ),
        },
    ],
    "contact": "contact@knowbear.app",
}

PRIVACY_POLICY = {
    "title": "Privacy Policy",
    "last_updated": "2026-03-29",
    "sections": [
        {
            "title": "Information We Collect",
            "body": (
                "We collect your email and basic profile details from Google Authentication, "
                "usage data to improve the Service, and subscription metadata from Dodo Payments. "
                "We do not store full payment card details."
            ),
        },
        {
            "title": "How We Use Information",
            "body": (
                "We use data to provide and secure access, process subscriptions, improve the "
                "Service, and communicate updates or billing notices."
            ),
        },
        {
            "title": "Service Providers",
            "body": (
                "Supabase provides authentication and database hosting, and Dodo Payments provides "
                "payment processing. These processors handle data under contractual obligations "
                "consistent with this policy."
            ),
        },
        {
            "title": "Cookies",
            "body": (
                "We use essential cookies to maintain sessions and improve reliability. "
                "You can control cookies via your browser settings."
            ),
        },
        {
            "title": "Your Rights (GDPR/CCPA)",
            "body": (
                "You may request access, correction, deletion, or portability of your data, and "
                "opt out of the sale of personal information (we do not sell it)."
            ),
        },
    ],
    "contact": "contact@knowbear.app",
}


@router.get("/legal/terms")
async def get_terms():
    return TERMS_OF_SERVICE


@router.get("/legal/privacy")
async def get_privacy():
    return PRIVACY_POLICY
