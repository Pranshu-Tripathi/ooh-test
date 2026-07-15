import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiTarget = process.env.OOH_API_PROXY_TARGET ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/agent-artifacts": apiTarget,
      "/agent-runs": apiTarget,
      "/agent-steps": apiTarget,
      "/generated-tests": apiTarget,
      "/jobs": apiTarget,
      "/repositories": apiTarget,
      "/runtime": apiTarget
    }
  }
});
