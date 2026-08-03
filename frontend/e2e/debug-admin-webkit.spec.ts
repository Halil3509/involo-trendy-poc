import { expect, test } from "@playwright/test";
import { API_BASE, mockApi } from "./helpers/routes";

test.describe("Debug admin scraper in webkit", () => {
  test("logs save config request", async ({ page }) => {
    page.on("request", (request) =>
      console.log(`[req] ${request.method()} ${request.url()}`),
    );
    page.on("response", (response) =>
      console.log(`[res] ${response.status()} ${response.url()}`),
    );
    page.on("requestfinished", (request) =>
      console.log(`[fin] ${request.method()} ${request.url()}`),
    );
    page.on("requestfailed", (request) =>
      console.log(`[fail] ${request.method()} ${request.url()} ${request.failure()?.errorText}`),
    );
    page.on("console", (msg) => console.log(`[console] ${msg.type()}: ${msg.text()}`));

    await mockApi(page);
    await page.goto("/admin/scraper");

    await expect(page.getByRole("heading", { name: "Configuration" })).toBeVisible();
    await expect(page.getByText("fashion")).toBeVisible();

    const keywordInput = page.getByLabel("Discovery keywords");
    await keywordInput.fill("sustainable style");
    await keywordInput.press("Enter");
    await expect(page.getByText("sustainable style")).toBeVisible();

    const savePromise = page.waitForRequest(`${API_BASE}/api/v1/admin/scraper/config`);
    await page.getByRole("button", { name: "Save configuration" }).click();
    const request = await savePromise;
    expect(request.method()).toBe("PUT");
    await expect(page.getByText("Scraper configuration saved.")).toBeVisible();
  });
});
