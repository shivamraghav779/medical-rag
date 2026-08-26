import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toApiError } from "../api/client";
import { useAuth } from "../context/AuthContext";

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await register(email, password, fullName);
      navigate("/chat");
    } catch (err) {
      // See LoginPage: the generic UNAUTHORIZED mapping is for expired
      // sessions, not applicable before one exists. The backend's own
      // message (e.g. "Email already registered.") is what belongs here.
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
          <h1 className="text-2xl font-bold text-oky-text">Create account</h1>
          <p className="mt-1 text-sm text-oky-muted">Clinical decision support platform</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="register-name" className="mb-1.5 block text-xs font-medium text-oky-text-secondary">
              Full name
            </label>
            <input
              id="register-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="input-field"
              autoComplete="name"
            />
          </div>
          <div>
            <label htmlFor="register-email" className="mb-1.5 block text-xs font-medium text-oky-text-secondary">
              Email
            </label>
            <input
              id="register-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="input-field"
              autoComplete="email"
              aria-describedby={error ? "register-error" : undefined}
            />
          </div>
          <div>
            <label htmlFor="register-password" className="mb-1.5 block text-xs font-medium text-oky-text-secondary">
              Password (min 8 chars)
            </label>
            <input
              id="register-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="input-field"
              autoComplete="new-password"
              aria-describedby={error ? "register-error" : undefined}
            />
          </div>
          {error && (
            <p
              id="register-error"
              role="alert"
              className="rounded-xl border border-danger-500/20 bg-danger-50 px-3 py-2 text-sm text-danger-700"
            >
              {error}
            </p>
          )}
          <button type="submit" disabled={loading} className="btn-primary w-full">
            {loading ? "Creating…" : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-oky-muted">
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-oky-purple hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
