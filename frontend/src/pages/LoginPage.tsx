import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toApiError } from "../api/client";
import { useAuth } from "../context/AuthContext";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/chat");
    } catch (err) {
      // Not errorMessage(): its UNAUTHORIZED mapping is written for an
      // *expired* session on an already-authenticated request ("Your
      // session expired. Please sign in again.") — on this page there is
      // no session yet, so a 401 here always means bad credentials. The
      // backend's own message ("Invalid email or password.") is correct
      // as-is; this page just needs to not overwrite it.
      setError(toApiError(err).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-mesh-gradient px-4">
      <div className="glass-card w-full max-w-md p-8">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-btn-gradient text-lg font-bold text-white shadow-lg">
            CR
          </div>
          <h1 className="text-2xl font-bold text-oky-text">Welcome back</h1>
          <p className="mt-1 text-sm text-oky-muted">Sign in to Clinical RAG</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="login-email" className="mb-1.5 block text-xs font-medium text-oky-text-secondary">
              Email
            </label>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="input-field"
              autoComplete="email"
              aria-describedby={error ? "login-error" : undefined}
            />
          </div>
          <div>
            <label htmlFor="login-password" className="mb-1.5 block text-xs font-medium text-oky-text-secondary">
              Password
            </label>
            <input
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="input-field"
              autoComplete="current-password"
              aria-describedby={error ? "login-error" : undefined}
            />
          </div>
          {error && (
            <p
              id="login-error"
              role="alert"
              className="rounded-xl border border-danger-500/20 bg-danger-50 px-3 py-2 text-sm text-danger-700"
            >
              {error}
            </p>
          )}
          <button type="submit" disabled={loading} className="btn-primary w-full">
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-oky-muted">
          No account?{" "}
          <Link to="/register" className="font-medium text-oky-purple hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}
