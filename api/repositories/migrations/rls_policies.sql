-- Enable RLS on tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE history ENABLE ROW LEVEL SECURITY;

-- Users Table Policies
-- Allow users to view their own profile
CREATE POLICY "Users can view own profile" 
ON users FOR SELECT 
TO authenticated 
USING (auth.uid() = id);

-- Allow users to update their own profile
CREATE POLICY "Users can update own profile" 
ON users FOR UPDATE 
TO authenticated 
USING (auth.uid() = id);

-- Allow users to insert their own profile (e.g. on first login via trigger or client)
CREATE POLICY "Users can insert own profile" 
ON users FOR INSERT 
TO authenticated 
WITH CHECK (auth.uid() = id);

-- History Table Policies
-- Allow users to view their own history
CREATE POLICY "Users can view own history" 
ON history FOR SELECT 
TO authenticated 
USING (auth.uid() = user_id);

-- Allow users to insert into their own history
CREATE POLICY "Users can insert own history" 
ON history FOR INSERT 
TO authenticated 
WITH CHECK (auth.uid() = user_id);

-- Allow users to delete their own history
CREATE POLICY "Users can delete own history" 
ON history FOR DELETE 
TO authenticated 
USING (auth.uid() = user_id);
