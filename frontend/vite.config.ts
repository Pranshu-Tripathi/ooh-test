import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.OOH_API_PROXY_TARGET ?? "http://localhost:8500";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8502,
    strictPort: true,
    proxy: {
      "/agent-artifacts": apiTarget,
      "/agent-runs": apiTarget,
      "/agent-steps": apiTarget,
      "/generated-tests": apiTarget,
      "/jobs": apiTarget,
      "/repositories": apiTarget,
      "/runtime": apiTarget
    }
  },
  preview: {
    port: 8503,
    strictPort: true
  }
});
