// AI contribution: 50% or more AI-generated
import { type FormEvent, useState } from "react";
import { type SignedInUser, signIn } from "../../services/api";
import { ErrorMessage } from "../ErrorMessage";
import "./SignInForm.css";

interface SignInFormProps {
  onSignedIn: (user: SignedInUser) => void;
}

export function SignInForm({ onSignedIn }: SignInFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    signIn(email, password)
      .then(onSignedIn)
      .catch((err: unknown) => {
        setError(err);
        setPassword("");
      })
      .finally(() => setBusy(false));
  };

  return (
    <form className="sign-in" onSubmit={submit} aria-label="Sign in">
      <h2>Sign in</h2>
      <label>
        Email
        <input
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </label>
      <label>
        Password
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
      <button type="submit" disabled={busy}>
        {busy ? "Signing in..." : "Sign in"}
      </button>
      {error !== null && <ErrorMessage error={error} />}
    </form>
  );
}
