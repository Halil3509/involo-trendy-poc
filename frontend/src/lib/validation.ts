import type { Job } from "@/lib/types";

export type AuthFields = {
  email: string;
  password: string;
  confirmPassword?: string;
};

export type AuthErrors = Partial<Record<keyof AuthFields, string>>;

export function validateAuth(
  values: AuthFields,
  isRegister: boolean,
): AuthErrors {
  const errors: AuthErrors = {};
  const email = values.email.trim();

  if (!email) errors.email = "Email is required.";
  else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    errors.email = "Enter a valid email address.";
  }

  if (!values.password) errors.password = "Password is required.";
  else if (values.password.length < 10) {
    errors.password = "Password must be at least 10 characters.";
  }

  if (isRegister && values.confirmPassword !== values.password) {
    errors.confirmPassword = "Passwords do not match.";
  }
  return errors;
}

export function normalizeKeyword(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function addKeyword(keywords: string[], value: string): string[] {
  const keyword = normalizeKeyword(value);
  if (!keyword) return keywords;
  if (keywords.some((item) => item.toLowerCase() === keyword.toLowerCase())) {
    return keywords;
  }
  return [...keywords, keyword];
}

const ACTIVE_STATES = new Set([
  "queued",
  "running",
  "fetching",
  "fetched",
  "analyzing",
  "reporting",
  "analyzed",
]);

export function isRunActive(run?: Job | null): boolean {
  return Boolean(run && ACTIVE_STATES.has(run.state.toLowerCase()));
}

export function runErrors(run?: Job | null): string[] {
  if (!run?.error) return [];
  return [run.error];
}

export function isValidCron(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return true; // empty clears the schedule
  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) return false;

  const ranges: Array<[number, number]> = [
    [0, 59],
    [0, 23],
    [1, 31],
    [1, 12],
    [0, 7],
  ];
  return fields.every((field, index) =>
    field.split(",").every((part) => isValidCronPart(part, ...ranges[index])),
  );
}

function isValidCronPart(part: string, min: number, max: number): boolean {
  const [base, step, extra] = part.split("/");
  if (extra !== undefined || (step !== undefined && !inRange(step, 1, max))) {
    return false;
  }
  if (base === "*") return true;
  const range = base.split("-");
  if (range.length === 1) return inRange(range[0], min, max);
  if (range.length !== 2) return false;
  const start = Number(range[0]);
  const end = Number(range[1]);
  return (
    inRange(range[0], min, max) &&
    inRange(range[1], min, max) &&
    start <= end
  );
}

function inRange(value: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(value)) return false;
  const number = Number(value);
  return number >= min && number <= max;
}
