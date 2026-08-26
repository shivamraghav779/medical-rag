import { Navigate, Outlet } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

type Props = {
  roles: Array<"user" | "agent" | "admin">;
  fallback?: string;
};

/** Blocks routes unless the signed-in user has one of the allowed roles. */
export default function RoleRoute({ roles, fallback = "/chat" }: Props) {
  const { user, loading, isAuthenticated } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-mesh-gradient">
        <Loader2 className="h-8 w-8 animate-spin text-oky-purple" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  const role = user?.role || "user";
  if (!roles.includes(role)) {
    return <Navigate to={fallback} replace />;
  }

  return <Outlet />;
}
