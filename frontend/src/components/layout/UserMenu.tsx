import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, ChevronDown, LogOut } from "lucide-react";
import type { User as UserType } from "../../types/auth";

interface Props {
  user: UserType | null;
  onLogout: () => void;
}

export default function UserMenu({ user, onLogout }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const initials = (user?.full_name || user?.email || "U")
    .split(" ")
    .map((s) => s[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full border border-oky-border/50 bg-white/80 py-1 pl-1 pr-2 shadow-sm transition hover:bg-white hover:shadow-md"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-btn-gradient text-xs font-bold text-white">
          {initials}
        </div>
        <ChevronDown
          className={`h-4 w-4 text-oky-muted transition ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-2 w-56 overflow-hidden rounded-2xl border border-oky-border/60 bg-white shadow-lg"
        >
          <div className="border-b border-oky-border/40 px-4 py-3">
            <p className="truncate text-sm font-semibold text-oky-text">
              {user?.full_name || "User"}
            </p>
            <p className="truncate text-xs text-oky-muted">{user?.email}</p>
          </div>
          <div className="p-1.5">
            <Link
              to="/status"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-oky-text-secondary transition hover:bg-oky-purple/5 hover:text-oky-purple"
            >
              <Activity className="h-4 w-4" />
              Usage & status
            </Link>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onLogout();
              }}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-red-500 transition hover:bg-red-50"
            >
              <LogOut className="h-4 w-4" />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
