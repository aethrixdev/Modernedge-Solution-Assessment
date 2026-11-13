import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, /api calls go to the FastAPI server on :8000.
export default defineConfig({
  plugins: [react()],
  server: { port: 3000, proxy: { "/api": "http://localhost:8000" } },
});
