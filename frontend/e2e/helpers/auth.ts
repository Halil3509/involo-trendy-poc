import type { Page } from "@playwright/test";

/**
 * Default admin credentials loaded from the root `.env` file.
 * Falls back to the E2E fixture values when the env vars are not set.
 */
export const ADMIN_EMAIL = process.env.INVOLO_BOOTSTRAP_ADMIN_EMAIL ?? "admin@e2e.local";
export const ADMIN_PASSWORD = process.env.INVOLO_BOOTSTRAP_ADMIN_PASSWORD ?? "password";

/**
 * Perform a UI login when the application lands on the sign-in screen.
 * This lets Playwright recover automatically if an E2E test starts
 * unauthenticated (e.g. when a token expires mid-run).
 */
export async function loginAsAdmin(
  page: Page,
  email = ADMIN_EMAIL,
  password = ADMIN_PASSWORD,
) {
  // Wait for the login form to be present before filling it in.
  await page.waitForURL(/\/login$/, { timeout: 5_000 }).catch(() => undefined);

  const emailInput = page.getByLabel("Email address");
  const passwordInput = page.getByLabel("Password");

  await emailInput.fill(email);
  await passwordInput.fill(password);

  // Submit the form directly. The button can be replaced with a spinner while
  // the request is in flight, which detaches it from the DOM, so using the
  // form avoids Playwright actionability retries against a detached element.
  // "commit" waits for the Next.js client-side navigation to start; the full
  // page load event is not emitted for SPA route replacements.
  await page.evaluate(() => {
    const form = document.querySelector("form") as HTMLFormElement | null;
    form?.requestSubmit();
  });
  // Next.js client-side navigation is not always visible to Playwright's
  // page.waitForURL in all browsers. Poll the location directly instead.
  await page.waitForFunction(
    () => !window.location.pathname.startsWith("/login"),
    null,
    { timeout: 10_000 },
  );
}

/**
 * Ensure the current page is authenticated. If a protected route redirects
 * to `/login`, this helper fills the form using `.env` credentials and
 * continues, otherwise it is a no-op.
 */
export async function ensureAuthenticated(page: Page) {
  const url = page.url();
  if (url.endsWith("/login") || url.includes("/login")) {
    await loginAsAdmin(page);
  }
}
