-- Ensure users/history tables are readable by their owners (local dev safety).

ALTER TABLE IF EXISTS public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.history ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'users'
      AND policyname = 'users_select_own'
  ) THEN
    CREATE POLICY users_select_own
      ON public.users
      FOR SELECT
      TO authenticated
      USING (auth.uid() = id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'users'
      AND policyname = 'users_update_own'
  ) THEN
    CREATE POLICY users_update_own
      ON public.users
      FOR UPDATE
      TO authenticated
      USING (auth.uid() = id)
      WITH CHECK (auth.uid() = id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'users'
      AND policyname = 'users_insert_own'
  ) THEN
    CREATE POLICY users_insert_own
      ON public.users
      FOR INSERT
      TO authenticated
      WITH CHECK (auth.uid() = id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'history'
      AND policyname = 'history_select_own'
  ) THEN
    CREATE POLICY history_select_own
      ON public.history
      FOR SELECT
      TO authenticated
      USING (auth.uid() = user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'history'
      AND policyname = 'history_insert_own'
  ) THEN
    CREATE POLICY history_insert_own
      ON public.history
      FOR INSERT
      TO authenticated
      WITH CHECK (auth.uid() = user_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'history'
      AND policyname = 'history_delete_own'
  ) THEN
    CREATE POLICY history_delete_own
      ON public.history
      FOR DELETE
      TO authenticated
      USING (auth.uid() = user_id);
  END IF;
END
$$;
