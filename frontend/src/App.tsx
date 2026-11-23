import { useAuth } from "./auth/AuthContext";
import { LoginScreen } from "./auth/LoginScreen";
import { PrismMark } from "./components/PrismMark";
import { Workspace } from "./shell/Workspace";

export default function App() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="reading" style={{ minHeight: "100vh" }}>
        <PrismMark size={56} />
      </div>
    );
  }
  if (!user) return <LoginScreen />;
  if (user.workspaces.length === 0) {
    return (
      <div className="reading" style={{ minHeight: "100vh" }}>
        <div>
          <PrismMark size={56} />
          <h2 className="display">No workspace yet</h2>
          <p className="dim">Ask an administrator to add you to a workspace.</p>
        </div>
      </div>
    );
  }
  return <Workspace key={user.id} />;
}
