import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo (npm run dev) el proxy apunta al stack de docker compose publicado en :8080.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/graphql": "http://localhost:8080" } },
});
