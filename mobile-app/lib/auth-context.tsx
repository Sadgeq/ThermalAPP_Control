import React, { createContext, useContext, useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import type { Session, User } from "@supabase/supabase-js";

type AuthState = {
  session: Session | null;
  user: User | null;
  loading: boolean;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({
  session: null,
  user: null,
  loading: true,
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    let resolved = false;
    const SESSION_TIMEOUT_MS = 4000;

    const finish = (s: Session | null) => {
      if (!mounted || resolved) return;
      resolved = true;
      setSession(s);
      setLoading(false);
    };

    supabase.auth
      .getSession()
      .then(({ data: { session } }) => finish(session))
      .catch((err) => {
        console.warn("[Auth] getSession failed:", err);
        finish(null);
      });

    // Expired session + flaky network hangs getSession indefinitely otherwise.
    const timeoutId = setTimeout(() => {
      if (!resolved) console.warn("[Auth] getSession timed out");
      finish(null);
    }, SESSION_TIMEOUT_MS);

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (mounted) setSession(session);
    });

    return () => {
      mounted = false;
      clearTimeout(timeoutId);
      subscription.unsubscribe();
    };
  }, []);

  const signOut = async () => {
    await supabase.auth.signOut();
    setSession(null);
  };

  return (
    <AuthContext.Provider
      value={{
        session,
        user: session?.user ?? null,
        loading,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
