"""Payment processing endpoints."""

import structlog
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from config import get_settings
from auth import verify_token

logger = structlog.get_logger()

router = APIRouter(tags=["payments"])


class CheckoutRequest(BaseModel):
    """Request model for creating a checkout session."""
    plan: str = "pro"  # Future-proof for multiple plans
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    """Response model for checkout session."""
    checkout_url: str
    session_id: str


@router.post("/payments/create-checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    request: CheckoutRequest,
    auth = Depends(verify_token)
):
    """
    Create a Dodo Payments checkout URL using payment links.
    
    Returns a checkout URL that the user should be redirected to.
    """
    logger.info("create_checkout_session_called", user_id=str(auth["user"].id))
    settings = get_settings()
    
    # Use Dodo Payment Link from environment configuration
    base_payment_link = f"https://pay.dodopayments.com/{settings.dodo_payment_link_id}"
    
    # Add customer email and metadata as URL parameters
    import urllib.parse
    params = {
        "prefilled_email": auth["user"].email,
        "customer_name": auth["user"].user_metadata.get("full_name", ""),
        "metadata[user_id]": str(auth["user"].id),
        "metadata[plan]": request.plan,
        "success_url": request.success_url or "https://knowbear.vercel.app/success",
        "cancel_url": request.cancel_url or "https://knowbear.vercel.app/app"
    }
    
    checkout_url = f"{base_payment_link}?{urllib.parse.urlencode(params)}"
    
    logger.info(
        "payment_link_generated",
        user_id=str(auth["user"].id),
        checkout_url=checkout_url
    )
    
    return CheckoutResponse(
        checkout_url=checkout_url,
        session_id=f"pl_{str(auth['user'].id)}"  # Use user ID as session reference
    )


@router.get("/payments/verify-status")
async def verify_payment_status(auth = Depends(verify_token)):
    """
    Verify the current Pro status of a user.
    
    This endpoint can be called after a successful payment to confirm
    that the webhook has processed and the user has been upgraded.
    """
    from supabase import create_client
    settings = get_settings()
    
    supabase = create_client(
        settings.supabase_url,
        settings.supabase_service_role_key
    )
    
    try:
        result = supabase.table("users").select("is_pro").eq(
            "id", str(auth["user"].id)
        ).execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")
        
        is_pro = result.data[0].get("is_pro", False)
        
        return {
            "user_id": str(auth["user"].id),
            "is_pro": is_pro,
            "status": "active" if is_pro else "free"
        }
    
    except Exception as e:
        logger.error("payment_status_verification_error", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Failed to verify payment status"
        )
