import { test, expect } from "@playwright/test";
import { mockApi } from "./helpers/routes";

test.use({ storageState: "./e2e/.auth/admin.json" });

test.describe("debug", () => {
  test("log network for /admin", async ({ page }) => {
    page.on("request", (request) =>
      console.log(
        `[request] ${request.method()} ${request.url()} headers=${JSON.stringify(request.headers())}`,
      ),
    );
    page.on("response", (response) =>
      console.log(
        `[response] ${response.status()} ${response.url()} ${response.request().method()}`,
      ),
    );
    page.on("requestfailed", (request) =>
      console.log(
        `[failed] ${request.method()} ${request.url()} ${request.failure()?.errorText}`,
      ),
    );
    page.on("console", (msg) => console.log(`[console] ${msg.type()}: ${msg.text()}`));

    await mockApi(page);
    await page.goto("/admin");
    await page.waitForTimeout(3000);
    await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible({ timeout: 1000 });
  });
});
