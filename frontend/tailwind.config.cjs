/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      colors: {
        // "oky-purple" etc. keep their original names so no component needs
        // renaming — only the hex values changed, retinting the whole app
        // from the old purple identity to a deep clinical teal in one place.
        // (UX_AUDIT.md: primary rebrand, approved teal direction.)
        oky: {
          bg: "#f4f9f8",
          surface: "#ffffff",
          card: "rgba(255, 255, 255, 0.72)",
          border: "rgba(13, 148, 136, 0.14)",
          purple: "#0d9488",
          "purple-light": "#5eead4",
          "purple-dark": "#115e59",
          pink: "#0891b2",
          muted: "#64748b",
          text: "#0f2b29",
          "text-secondary": "#475569",
        },
        // Semantic status colors — explicit and never reused for a different
        // meaning. Each has a full 50/500/700 trio so components can pick
        // subtle-background + solid-text pairings consistently.
        success: {
          50: "#f0fdf4",
          500: "#22c55e",
          700: "#15803d",
        },
        warning: {
          50: "#fffbeb",
          500: "#f59e0b",
          700: "#b45309",
        },
        danger: {
          50: "#fef2f2",
          500: "#ef4444",
          700: "#b91c1c",
        },
        info: {
          50: "#eff6ff",
          500: "#3b82f6",
          700: "#1d4ed8",
        },
        // Reserved exclusively for "a human agent is active" signals (chat
        // bubbles, banners, input border during HUMAN_ACTIVE) — deliberately
        // the old brand purple, so it still reads as a distinct, warm,
        // human accent against the new teal/AI primary.
        agent: {
          50: "#f5f3ff",
          400: "#a78bfa",
          500: "#7c3aed",
          700: "#5b21b6",
        },
      },
      fontSize: {
        // Typography scale — see tokens.css for usage notes per step.
        "page-title": ["1.75rem", { lineHeight: "1.2", fontWeight: "700" }],
        "section-heading": ["1.25rem", { lineHeight: "1.3", fontWeight: "600" }],
        "card-title": ["1rem", { lineHeight: "1.4", fontWeight: "600" }],
        body: ["0.9375rem", { lineHeight: "1.65" }],
        label: ["0.75rem", { lineHeight: "1.4", fontWeight: "500", letterSpacing: "0.02em" }],
        caption: ["0.6875rem", { lineHeight: "1.4" }],
        code: ["0.8125rem", { lineHeight: "1.5" }],
      },
      boxShadow: {
        glass: "0 8px 32px rgba(13, 148, 136, 0.08)",
        card: "0 4px 24px rgba(15, 43, 41, 0.06)",
        sidebar: "0 8px 32px rgba(17, 94, 89, 0.25)",
        input: "0 4px 20px rgba(13, 148, 136, 0.1)",
        // Named tiers per the shadow-system brief: none for flat elements
        // (use shadow-none), light for cards (shadow-card, above), medium
        // for modals/dropdowns.
        modal: "0 12px 40px rgba(15, 43, 41, 0.16)",
      },
      backgroundImage: {
        "sidebar-gradient": "linear-gradient(180deg, #115e59 0%, #0f766e 55%, #0d9488 100%)",
        "mesh-gradient":
          "radial-gradient(ellipse at 20% 20%, rgba(94, 234, 212, 0.30) 0%, transparent 50%), radial-gradient(ellipse at 80% 10%, rgba(8, 145, 178, 0.16) 0%, transparent 45%), radial-gradient(ellipse at 60% 80%, rgba(13, 148, 136, 0.14) 0%, transparent 50%), #f4f9f8",
        "btn-gradient": "linear-gradient(135deg, #0d9488 0%, #0f766e 100%)",
      },
      animation: {
        "pulse-soft": "pulse-soft 2s ease-in-out infinite",
        "slide-in-left": "slide-in-left 0.25s ease-out",
        "slide-in-right": "slide-in-right 0.25s ease-out",
        "fade-in": "fade-in 0.2s ease-out",
        "count-up": "fade-in 0.4s ease-out",
      },
      keyframes: {
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },
        // New chat messages arrive from their sender's side — bot/agent
        // from the left, the patient's own messages from the right
        // (UX_AUDIT.md: micro-interactions).
        "slide-in-left": {
          from: { opacity: "0", transform: "translateX(-8px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        "slide-in-right": {
          from: { opacity: "0", transform: "translateX(8px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
      },
      borderRadius: {
        "4xl": "2rem",
      },
      transitionDuration: {
        DEFAULT: "150ms",
      },
      transitionTimingFunction: {
        DEFAULT: "ease-in-out",
      },
    },
  },
  plugins: [],
};
