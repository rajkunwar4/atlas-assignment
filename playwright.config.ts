import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:5174", trace: "retain-on-failure" },
  webServer: [
    {
      command:
        "npx dotenv -e .env -- python3 -m uvicorn app.main:app --app-dir apps/api-python --port 8100",
      url: "http://127.0.0.1:8100/api/health",
      reuseExistingServer: false,
    },
    {
      command:
        "env VITE_API_URL=http://127.0.0.1:8100 npm --workspace apps/web run dev -- --port 5174",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
    },
  ],
});
