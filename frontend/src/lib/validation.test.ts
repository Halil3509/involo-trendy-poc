import {
  addKeyword,
  isRunActive,
  isValidCron,
  runErrors,
  validateAuth,
} from "@/lib/validation";
import type { Job } from "@/lib/types";

const job = (overrides: Partial<Job>): Job => ({
  id: "job-1",
  kind: "scrape",
  state: "queued",
  counters: {},
  ...overrides,
});

describe("auth validation", () => {
  it("reports invalid registration fields", () => {
    expect(
      validateAuth(
        {
          email: "not-an-email",
          password: "short",
          confirmPassword: "different",
        },
        true,
      ),
    ).toEqual({
      email: "Enter a valid email address.",
      password: "Password must be at least 10 characters.",
      confirmPassword: "Passwords do not match.",
    });
  });

  it("accepts a valid login", () => {
    expect(
      validateAuth(
        { email: "user@example.com", password: "password123" },
        false,
      ),
    ).toEqual({});
  });
});

describe("scraper helpers", () => {
  it("normalizes and deduplicates keywords case-insensitively", () => {
    expect(addKeyword(["Fashion"], "  sustainable   style ")).toEqual([
      "Fashion",
      "sustainable style",
    ]);
    expect(addKeyword(["Fashion"], "fashion")).toEqual(["Fashion"]);
  });

  it("identifies active runs and normalizes errors", () => {
    expect(isRunActive(job({ state: "RUNNING" }))).toBe(true);
    expect(isRunActive(job({ state: "succeeded" }))).toBe(false);
    expect(
      runErrors(job({ state: "failed", error: "Browser failed" })),
    ).toEqual(["Browser failed"]);
  });

  it("validates cron expressions", () => {
    expect(isValidCron("0 5 * * *")).toBe(true);
    expect(isValidCron("*/15 0-23 * * 1,3,5")).toBe(true);
    expect(isValidCron("")).toBe(true);
    expect(isValidCron("0 5 * *")).toBe(false);
    expect(isValidCron("60 5 * * *")).toBe(false);
    expect(isValidCron("* * 0 * *")).toBe(false);
  });
});
