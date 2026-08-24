import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:5173", trace: "retain-on-failure" },
  webServer: [
    {
      command:
        "npm run reset:python && python3 -m uvicorn app.main:app --app-dir apps/api-python --port 8000",
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: true,
    },
    {
      command: "npm run reset:node && npm --workspace apps/api-node start",
      url: "http://127.0.0.1:8001/api/health",
      reuseExistingServer: true,
    },
    {
      command: "npm run dev:web",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
    },
  ],
  projects: [
    { name: "python", metadata: { backend: "Python · FastAPI" } },
    { name: "node", metadata: { backend: "Node · Fastify" } },
  ],
});
