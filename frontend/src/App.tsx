import { useEffect, useState } from "react";
import { SignInForm } from "./components/auth/SignInForm";
import { ErrorMessage } from "./components/ErrorMessage";
import { SyncRunsPage } from "./components/syncRuns/SyncRunsPage";
import {
  ApiError,
  getHealth,
  getMe,
  onUnauthorized,
  signOut,
  type SignedInUser,
} from "./services/api";

function App() {
  const [status, setStatus] = useState("Connecting...");
  const [error, setError] = useState<unknown>(null);
  // Undefined while the session is being checked, null when signed out.
  const [user, setUser] = useState<SignedInUser | null | undefined>(undefined);

  useEffect(() => {
    getHealth()
      .then((data) => setStatus(data.status))
      .catch((err: unknown) => {
        setStatus("Backend unavailable");
        setError(err);
      });
    getMe()
      .then(setUser)
      .catch((err: unknown) => {
        setUser(null);
        if (!(err instanceof ApiError && err.status === 401)) setError(err);
      });
    // A session that ends while the page is open sends the user back to sign in.
    onUnauthorized(() => setUser(null));
    return () => onUnauthorized(null);
  }, []);

  const leave = () => {
    signOut()
      .catch(() => undefined)
      .finally(() => setUser(null));
  };

  return (
    <main style={{ padding: "1.5rem 2rem", fontFamily: "Arial" }}>
      <h1>PULSE Dashboard</h1>
      <p>Ecosystem Intelligence Platform</p>
      <p>Backend status: {status}</p>
      {error !== null ? (
        <ErrorMessage error={error} />
      ) : user === undefined ? (
        <p>Checking your session...</p>
      ) : user === null ? (
        <SignInForm onSignedIn={setUser} />
      ) : (
        <>
          <p className="signed-in">
            Signed in as {user.email} ({user.tenant_name}){" "}
            <button type="button" onClick={leave}>
              Sign out
            </button>
          </p>
          <SyncRunsPage />
        </>
      )}
    </main>
  );
}

export default App;
