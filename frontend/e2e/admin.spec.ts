import { test, expect } from "@playwright/test";
import { mockApi } from "./helpers/routes";

test.describe("Admin overview", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await page.goto("/admin");
  });

  test("renders overview metrics, jobs, and attention items", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
    await expect(page.getByText("Users")).toBeVisible();
    await expect(page.getByText("7", { exact: true })).toBeVisible();
    await expect(page.getByText("1 need reauth")).toBeVisible();
    await expect(page.getByRole("cell", { name: "scrape" })).toBeVisible();
    await expect(page.getByText("boom")).toBeVisible();
    await expect(page.getByText("Quality gates passed")).toBeVisible();
  });
});
