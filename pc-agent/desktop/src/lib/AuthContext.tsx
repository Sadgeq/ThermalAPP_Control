import { createContext, useContext, useEffect, useRef, useState, ReactNode } from "react";
import { supabase } from "./supabase";
import type { Session, User } from "@supabase/supabase-js";
import { invoke } from "@tauri-apps/api/core";

type AuthState = {
  session: Session | null;
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({
  session: null,
  user: null,
  loading: true,
  signIn: async () => {},
  signUp: async () => {},
  signOut: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  // Track which user the agent is currently running for, so we don't
  // re-spawn it on every onAuthStateChange tick (Supabase fires those
  // periodically as it refreshes tokens).
  const spawnedForUser = useRef<string | null>(null);

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

    // Hard timeout: offline / expired-token refresh hangs forever otherwise.
    const timeoutId = window.setTimeout(() => {
      if (!resolved) console.warn("[Auth] getSession timed out, proceeding unauthenticated");
      finish(null);
    }, SESSION_TIMEOUT_MS);

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
    });

    return () => {
      mounted = false;
      window.clearTimeout(timeoutId);
      subscription.unsubscribe();
    };
  }, []);

  // Drive the bundled agent's lifecycle from the auth state. When the user
  // signs in, hand the access/refresh tokens to the Rust sidecar so the
  // agent boots already authenticated as this user — no PIN dance. On
  // sign-out, kill the agent so its cloud session can't outlive the UI's.
  // In dev mode (no Tauri runtime) the invoke calls fail silently and the
  // developer's hand-launched python agent.py handles auth via pairing.
  useEffect(() => {
    if (loading) return;

    if (session?.access_token && session?.refresh_token) {
      // Same user as last spawn? Skip — Supabase fires onAuthStateChange on
      // every token refresh, but the underlying user is unchanged.
      if (spawnedForUser.current === session.user.id) return;
      spawnedForUser.current = session.user.id;
      invoke("start_agent_with_session", {
        accessToken: session.access_token,
        refreshToken: session.refresh_token,
      }).catch((err) => {
        // Non-fatal: the desktop UI works without the bundled agent (the
        // user can run python Backend/agent.py manually). Log so we can
        // diagnose if a real bundle fails to launch.
        console.warn("[Auth] start_agent_with_session failed:", err);
      });
    } else {
      if (spawnedForUser.current === null) return;
      spawnedForUser.current = null;
      invoke("stop_agent").catch(() => {
        // Same as above — fine if Tauri isn't there or the agent wasn't running.
      });
    }
  }, [session, loading]);

  const signIn = async (email: string, password: string) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw error;
  };

  const signUp = async (email: string, password: string) => {
    const { error } = await supabase.auth.signUp({ email, password });
    if (error) throw error;
  };

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
        signIn,
        signUp,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);