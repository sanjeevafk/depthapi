-- Create a table for public profiles (links to auth.users)
create table if not exists public.users (
  id uuid not null references auth.users on delete cascade,
  email text,
  full_name text,
  avatar_url text,
  is_pro boolean default false,
  created_at timestamptz default now(),
  primary key (id)
);

-- Create a table for chat history
create table if not exists public.history (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references public.users(id) on delete cascade not null,
  topic text not null,
  levels text[] not null, -- Array of levels queried
  mode text default 'fast', -- Store which mode was used
  created_at timestamptz default now()
);

-- Set up Row Level Security (RLS)
-- (Make sure to run rls_policies.sql AFTER this script)
alter table public.users enable row level security;
alter table public.history enable row level security;
