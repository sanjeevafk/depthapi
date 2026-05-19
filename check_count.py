import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
res = supabase.table("knowledge_chunks").select("id", count="exact").limit(1).execute()
print("TOTAL CHUNKS IN SUPABASE:", res.count)
