import { useState, type FormEvent } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { useAuth } from "./AuthContext";
import { PrismMark } from "../components/PrismMark";

export function LoginScreen() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reduce = useReducedMotion();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <section className="login-art" aria-hidden="true">
        <PrismMark size={64} animate={!reduce} />
        <h1>
          Give PRISM
          <br />
          any data.
        </h1>
        <p className="dim" style={{ maxWidth: 460, fontSize: 16 }}>
          It reads the mess, finds the structure, does the arithmetic deterministically, and hands back a workbook you
          can trust — in Lao and English.
        </p>
        <div style={{ display: "flex", gap: 8, marginTop: 24 }}>
          <span className="pill pill-aurora"><span className="dot" />live data</span>
          <span className="pill pill-nebula"><span className="dot" />AI-inferred</span>
          <span className="pill pill-ember"><span className="dot" />needs attention</span>
        </div>
      </section>
      <motion.form
        className="login-form"
        onSubmit={submit}
        initial={reduce ? false : { opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      >
        <h2 className="display" style={{ fontSize: 28, margin: "0 0 4px" }}>Sign in</h2>
        <p className="dim" style={{ margin: "0 0 10px" }}>Use the account your administrator created.</p>
        <label>
          Email
          <input className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="error-box" role="alert">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy} style={{ justifyContent: "center", height: 40 }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </motion.form>
    </div>
  );
}
