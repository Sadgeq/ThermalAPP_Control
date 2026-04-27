import { createClient } from "@supabase/supabase-js";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { openUrl } from "@tauri-apps/plugin-opener";

const SUPABASE_URL = "https://gwpqkvsvhobkkqctjduc.supabase.co";
const SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd3cHFrdnN2aG9ia2txY3RqZHVjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIwMzk0NTQsImV4cCI6MjA4NzYxNTQ1NH0.OcEe0CphKJ4Lu7jwPrwJ2SdOiWjRwn3Vtc8Nur4oB1I";

// flowType: 'pkce' is required so signInWithOAuth redirects to
// redirectTo?code=XXX (query string, catchable by the loopback server)
// instead of the default implicit flow with tokens in the URL hash
// (hashes never reach the server — the loopback would catch nothing).
export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    flowType: "pkce",
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false, // We handle the OAuth callback manually
  },
});

// Fixed loopback port so it can be whitelisted in Supabase's redirect URL
// settings (dynamic ports won't work — Supabase would reject the redirect).
const OAUTH_PORT = 54321;
const OAUTH_REDIRECT = `http://localhost:${OAUTH_PORT}`;

const OAUTH_SUCCESS_HTML = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Signed in</title>
<style>
  body { font-family: system-ui, sans-serif; background: #111; color: #eee;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; margin: 0; text-align: center; }
  .card { padding: 32px 40px; border-radius: 12px; background: #1c1c1c; }
  h1 { margin: 0 0 8px; font-size: 18px; font-weight: 600; }
  p { margin: 0; font-size: 13px; color: #888; }
</style></head><body><div class="card">
<h1>Signed in to THERM_OS</h1><p>You can close this tab and return to the app.</p>
</div></body></html>`;

/**
 * Kick off Google OAuth via Supabase + tauri-plugin-oauth loopback.
 * - Starts a local HTTP server on OAUTH_PORT
 * - Opens Supabase's OAuth URL in the system browser
 * - User authenticates with Google → Supabase → loopback
 * - Parses the auth code and exchanges it for a session
 */
export async function signInWithGoogle(): Promise<void> {
  // Start the plugin's loopback server on a fixed port.
  // Returns the actual bound port (should match OAUTH_PORT).
  const port = await invoke<number>("plugin:oauth|start", {
    config: {
      ports: [OAUTH_PORT],
      response: OAUTH_SUCCESS_HTML,
    },
  });

  // Promise that resolves with the callback URL or rejects on timeout.
  const callbackUrl = await new Promise<string>(async (resolve, reject) => {
    const TIMEOUT_MS = 120_000; // 2 minutes for the user to complete auth
    let done = false;

    const timeoutId = window.setTimeout(() => {
      if (!done) {
        done = true;
        invoke("plugin:oauth|cancel", { port }).catch(() => {});
        reject(new Error("OAuth sign-in timed out"));
      }
    }, TIMEOUT_MS);

    const unlisten = await listen<string>("oauth://url", (event) => {
      if (done) return;
      done = true;
      window.clearTimeout(timeoutId);
      unlisten();
      resolve(event.payload);
    });

    // Ask Supabase for the authorize URL (don't auto-redirect the webview)
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: OAUTH_REDIRECT,
        skipBrowserRedirect: true,
      },
    });
    if (error || !data?.url) {
      done = true;
      window.clearTimeout(timeoutId);
      unlisten();
      invoke("plugin:oauth|cancel", { port }).catch(() => {});
      reject(error ?? new Error("Supabase returned no OAuth URL"));
      return;
    }

    // Open Google/Supabase OAuth in the default browser.
    await openUrl(data.url);
  });

  // Parse the ?code=... param from the callback URL.
  const url = new URL(callbackUrl);
  const code = url.searchParams.get("code");
  const errParam = url.searchParams.get("error_description")
    ?? url.searchParams.get("error");
  if (errParam) throw new Error(`OAuth error: ${errParam}`);
  if (!code) throw new Error("OAuth callback missing 'code'");

  // Exchange the code for a session. Supabase uses the stored PKCE
  // code_verifier from localStorage to complete the exchange; onAuthStateChange
  // will fire and the AuthContext will populate session.
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) throw error;
}