import { test, expect } from "@playwright/test";
import { mockApi } from "./helpers/routes";

test.describe("Instagram OAuth callback", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
  });

  test("shows connected message", async ({ page }) => {
    await page.goto("/instagram/callback?instagram=connected");
    await expect(page.getByRole("heading", { name: "Instagram connected" })).toBeVisible();
    await expect(page.getByText("You can close this window and return to Involo.")).toBeVisible();
  });

  test("shows error message with custom text", async ({ page }) => {
    await page.goto("/instagram/callback?instagram=error&message=access%20denied");
    await expect(page.getByRole("heading", { name: "Connection failed" })).toBeVisible();
    await expect(page.getByText("access denied")).toBeVisible();
  });

  test("shows completing state without status", async ({ page }) => {
    await page.goto("/instagram/callback");
    await expect(page.getByRole("heading", { name: "Completing connection..." })).toBeVisible();
  });
});
