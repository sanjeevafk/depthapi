import sys
import os

# Ensure we can import from the api folder
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

try:
    from api.config import get_settings    
    from supabase import create_client
except ImportError as e:
    print(f"Error importing required modules: {e}")
    print("Make sure you are running this from the project root and dependencies are installed.")
    sys.exit(1)

def promote_user(email: str):
    print("Initializing Supabase client...")
    settings = get_settings()
    
    if not settings.supabase_url or not settings.supabase_secret_key:
        print("❌ Error: Missing SUPABASE_URL or SUPABASE_SECRET_KEY in .env")
        return

    # Initialize Supabase with Service Role Key (Bypasses RLS)
    supabase = create_client(settings.supabase_url, settings.supabase_secret_key)
    
    print(f"🔍 Searching for user: {email}...")
    
    # Query the 'users' table
    # Note: This is the public.users table we created, not auth.users
    try:
        response = supabase.table("users").select("*").eq("email", email).execute()
    except Exception as e:
        print(f"❌ Database error: {e}")
        return

    if not response.data:
        print(f"❌ User '{email}' not found in public.users table.")
        print("💡 Tip: You must log in to the app at least once to create your user record.")
        return
        
    user = response.data[0]
    if not isinstance(user, dict):
        print("❌ Unexpected user data format.")
        return
    user_id = user.get('id')
    user_id = user.get('id')
    current_status = user.get('is_pro', False)
    
    if not user_id:
        print("❌ User record is missing 'id' field.")
        return    print(f"✅ Found user: {user.get('full_name', 'Unknown')}")
    print(f"   ID: {user_id}")
    print(f"   Current Status: {'🌟 PRO' if current_status else '👤 FREE'}")
    
    if current_status:
        print("\n🎉 User is already a Pro member!")
        return

    print("\n🚀 Promoting user to Pro...")
    
    try:
        update_response = supabase.table("users").update({"is_pro": True}).eq("id", user_id).execute()
        
        if update_response.data:
            print(f"✅ Success! {email} is now a Pro user.")
            print("👉 Please REFRESH your browser or Log Out/Log In to see the changes.")
        else:
            print("❌ Failed to update user status.")
            
    except Exception as e:
        print(f"❌ Error updating user: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("\nUsage: python scripts/promote_user.py <your-email@example.com>")
        print("Example: python scripts/promote_user.py admin@depthapi.app\n")
        sys.exit(1)
        
    target_email = sys.argv[1]
    promote_user(target_email)
