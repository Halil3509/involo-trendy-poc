import { test, expect } from "@playwright/test";
import { API_BASE, mockApi } from "./helpers/routes";
import { profilingConfig } from "./helpers/fixtures";

test.describe("Admin profiling", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("loads capacity estimate and saves a valid schedule", async ({ page }) => {
    await page.goto("/admin/profiling", { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("heading", { name: "Capacity estimate" })).toBeVisible();
    await expect(page.getByText("12")).toBeVisible();

    const cronInput = page.getByLabel("Schedule (cron)");
    await cronInput.fill("");
    await cronInput.fill("*/30 * * * *");

    const savePromise = page.waitForRequest(`${API_BASE}/api/v1/admin/profiling/config`);
    await page.getByRole("button", { name: "Save schedule" }).click();
    const request = await savePromise;

    expect(request.method()).toBe("PUT");
    expect(await request.postDataJSON()).toMatchObject({
      enabled: profilingConfig.enabled,
      schedule_cron: "*/30 * * * *",
    });

    await expect(page.getByText("Profiling schedule saved.")).toBeVisible();
  });

  test("rejects invalid cron and starts a manual run", async ({ page }) => {
    await page.goto("/admin/profiling", { waitUntil: "domcontentloaded" });

    await page.getByLabel("Schedule (cron)").fill("90 * * * *");
    await expect(page.getByText("Enter a valid five-field cron expression.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save schedule" })).toBeDisabled();

    const startPromise = page.waitForRequest(`${API_BASE}/api/v1/admin/profiling/runs`);
    await page.getByRole("button", { name: "Run profiling now" }).click();
    const request = await startPromise;

    expect(request.method()).toBe("POST");
    await expect(page.getByText("Profiling run started.")).toBeVisible();
  });
});
