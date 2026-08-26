import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      // Trailing slash so /widget-demo is not swallowed by this proxy
      "/widget/": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
        changeOrigin: true,
        // Agent WS churn (Strict Mode double-mount, HMR, reconnects) writes to
        // a proxy socket after the underlying connection has already closed —
        // EPIPE/ECONNRESET here are expected noise during dev, not real
        // errors. Attaching any "error" listener also prevents these from
        // becoming an unhandled-error crash. (Issue 23) No @types/node in
        // this tsconfig scope, so the error param is intentionally untyped.
        configure: (proxy) => {
          const isBenign = (err: unknown) =>
            (err as { code?: string } | undefined)?.code === "EPIPE" ||
            (err as { code?: string } | undefined)?.code === "ECONNRESET";
          proxy.on("error", (err) => {
            if (isBenign(err)) return;
          });
          proxy.on("proxyReqWs", (proxyReq) => {
            proxyReq.on("error", (err) => {
              if (isBenign(err)) return;
            });
          });
        },
      },
    },
  },
});
