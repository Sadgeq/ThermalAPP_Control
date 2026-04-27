import "react-native-url-polyfill/auto";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = "https://gwpqkvsvhobkkqctjduc.supabase.co";
const SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd3cHFrdnN2aG9ia2txY3RqZHVjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIwMzk0NTQsImV4cCI6MjA4NzYxNTQ1NH0.OcEe0CphKJ4Lu7jwPrwJ2SdOiWjRwn3Vtc8Nur4oB1I";

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    storage: AsyncStorage,
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
  },
});
