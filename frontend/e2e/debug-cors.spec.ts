import { expect, test } from "@playwright/test";
import { API_BASE, mockApi } from "./helpers/routes";

test("debug CORS PUT in webkit", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (request) =>
    requests.push(`${request.method()} ${request.url()}`),
  );
  page.on("console", (msg) => console.log(`[console] ${msg.type()}: ${msg.text()}`));

  await mockApi(page);
  await page.goto("/login");
  const result = await page.evaluate(async (apiBase) => {
    try {
      const response = await fetch(`${apiBase}/api/v1/admin/scraper/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Bypass-Tunnel-Reminder": "true" },
        body: JSON.stringify({ keywords: ["x"], reels_per_keyword: 1, headless: true, viral_threshold: 0, schedule_cron: null, schedule_pipeline: false }),
        credentials: "include",
      });
      return { ok: response.ok, status: response.status, text: await response.text() };
    } catch (error) {
      return { error: String(error) };
    }
  }, API_BASE);
  console.log("fetch result:", JSON.stringify(result));
  console.log("requests:", JSON.stringify(requests.slice(-10)));
  expect(result).toMatchObject({ ok: true, status: 200 });
});
