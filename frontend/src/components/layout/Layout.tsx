import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  Activity,
  BarChart3,
  Bot,
  Code2,
  FileText,
  Headset,
  HelpCircle,
  Plus,
  Search,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useSession } from "../../context/SessionContext";
import ConversationSidebar from "../chat/ConversationSidebar";
import UserMenu from "./UserMenu";

// Grouped for visual scannability in the icon-only rail (UX_AUDIT.md:
// "group related navigation items visually") — a thin divider renders
// between groups, not a text header, since the rail has no room for labels.
const NAV_GROUPS = [
  {
    group: "Patient tools",
    items: [
      { to: "/chat", icon: Bot, label: "AI Chatbot" },
      { to: "/faq", icon: HelpCircle, label: "FAQ" },
    ],
  },
  {
    group: "Clinical data",
    items: [{ to: "/documents", icon: FileText, label: "Documents" }],
  },
  {
    group: "Monitoring",
    items: [
      { to: "/analytics", icon: BarChart3, label: "Analytics" },
      { to: "/status", icon: Activity, label: "Status" },
    ],
  },
  {
    group: "Administration",
    items: [
      { to: "/agent", icon: Headset, label: "Agent", roles: ["agent", "admin"] as const },
      { to: "/widget-demo", icon: Code2, label: "Widget demo" },
    ],
  },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const isChat = location.pathname.startsWith("/chat");
  const [chatSearch, setChatSearch] = useState("");
  const {
    conversationId,
    setConversationId,
    newConversation,
    conversationRefreshKey,
  } = useSession();

  const navGroups = NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((item) => {
      if (!("roles" in item) || !item.roles) return true;
      return !!user?.role && (item.roles as readonly string[]).includes(user.role);
    }),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="flex h-screen gap-3 overflow-hidden bg-mesh-gradient p-3">
      {/* Icon navigation rail */}
      <aside className="flex w-[72px] shrink-0 flex-col items-center rounded-3xl bg-sidebar-gradient py-5 shadow-sidebar">
        <div className="mb-6 flex h-10 w-10 items-center justify-center rounded-xl bg-white/20 text-sm font-bold text-white">
          CR
        </div>

        <nav className="flex flex-1 flex-col items-center gap-3">
          {navGroups.map((g, i) => (
            <div
              key={g.group}
              className={`flex flex-col items-center gap-2 ${
                i > 0 ? "mt-1 border-t border-white/15 pt-3" : ""
              }`}
            >
              {g.items.map(({ to, icon: Icon, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  title={label}
                  aria-label={label}
                  className={({ isActive }) =>
                    `nav-icon ${isActive ? "nav-icon-active" : ""}`
                  }
                >
                  <Icon className="h-5 w-5" />
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      {/* Conversation history — chat route only */}
      {isChat && (
        <aside className="glass-card flex w-[280px] shrink-0 flex-col overflow-hidden">
          <div className="shrink-0 border-b border-oky-border/50 px-4 py-4">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-semibold text-oky-text">Chats</p>
              <button
                type="button"
                onClick={() => {
                  setChatSearch("");
                  newConversation();
                }}
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-oky-purple/10 text-oky-purple transition hover:bg-oky-purple/20"
                title="New chat"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-oky-muted" />
              <input
                type="search"
                value={chatSearch}
                onChange={(e) => setChatSearch(e.target.value)}
                placeholder="Search chats..."
                className="w-full rounded-xl border border-oky-border/60 bg-white/90 py-2 pl-9 pr-3 text-sm text-oky-text placeholder:text-oky-muted focus:border-oky-purple/30 focus:outline-none focus:ring-2 focus:ring-oky-purple/10"
              />
            </div>
          </div>
          <ConversationSidebar
            activeId={conversationId}
            onSelect={setConversationId}
            refreshKey={conversationRefreshKey}
            searchQuery={chatSearch}
          />
        </aside>
      )}

      {/* Main workspace */}
      <div className="glass flex min-w-0 flex-1 flex-col overflow-hidden rounded-3xl">
        <header className="flex h-14 shrink-0 items-center justify-end border-b border-oky-border/40 px-6">
          <UserMenu user={user} onLogout={logout} />
        </header>

        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
