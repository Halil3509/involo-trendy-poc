"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { api } from "@/lib/api";
import {
  type AuthErrors,
  type AuthFields,
  validateAuth,
} from "@/lib/validation";

type AuthFormProps = {
  mode: "login" | "register";
};

export function AuthForm({ mode }: AuthFormProps) {
  const isRegister = mode === "register";
  const router = useRouter();
  const { refreshUser } = useAuth();
  const [values, setValues] = useState<AuthFields>({
    email: "",
    password: "",
    confirmPassword: "",
  });
  const [errors, setErrors] = useState<AuthErrors>({});
  const [serverError, setServerError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function setField(field: keyof AuthFields, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
    setServerError("");
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = validateAuth(values, isRegister);
    if (Object.keys(nextErrors).length) {
      setErrors(nextErrors);
      return;
    }

    setSubmitting(true);
    setServerError("");
    try {
      if (isRegister) {
        await api.register(values.email.trim(), values.password);
      } else {
        await api.login(values.email.trim(), values.password);
      }
      await refreshUser();
      router.replace(isRegister ? "/onboarding" : "/dashboard");
      router.refresh();
    } catch (error) {
      setServerError(
        error instanceof Error
          ? error.message
          : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <Link href="/login" className="brand" aria-label="Involo home">
          <span className="brand-mark">I</span>
          <span>involo</span>
        </Link>
        <div className="mt-10">
          <p className="eyebrow">
            {isRegister ? "Start discovering" : "Welcome back"}
          </p>
          <h1 id="auth-title" className="mt-2 text-3xl font-semibold tracking-tight">
            {isRegister ? "Create your account" : "Sign in to your workspace"}
          </h1>
          <p className="mt-3 text-sm leading-6 text-slate-500">
            {isRegister
              ? "Create an account to turn social signals into useful ideas."
              : "Continue to your content intelligence dashboard."}
          </p>
        </div>

        <form className="mt-8 space-y-5" onSubmit={handleSubmit} noValidate>
          {serverError && (
            <div className="alert alert-error" role="alert">
              {serverError}
            </div>
          )}

          <Field
            id="email"
            label="Email address"
            type="email"
            value={values.email}
            error={errors.email}
            autoComplete="email"
            onChange={(value) => setField("email", value)}
          />
          <Field
            id="password"
            label="Password"
            type="password"
            value={values.password}
            error={errors.password}
            autoComplete={isRegister ? "new-password" : "current-password"}
            hint={isRegister ? "Use at least 10 characters." : undefined}
            onChange={(value) => setField("password", value)}
          />
          {isRegister && (
            <Field
              id="confirm-password"
              label="Confirm password"
              type="password"
              value={values.confirmPassword ?? ""}
              error={errors.confirmPassword}
              autoComplete="new-password"
              onChange={(value) => setField("confirmPassword", value)}
            />
          )}

          <button className="button button-primary w-full" disabled={submitting}>
            {submitting && <span className="spinner spinner-light" />}
            {submitting
              ? isRegister
                ? "Creating account..."
                : "Signing in..."
              : isRegister
                ? "Create account"
                : "Sign in"}
          </button>
        </form>

        <p className="mt-8 text-center text-sm text-slate-500">
          {isRegister ? "Already have an account?" : "New to Involo?"}{" "}
          <Link
            className="font-semibold text-indigo-700 hover:text-indigo-600"
            href={isRegister ? "/login" : "/register"}
          >
            {isRegister ? "Sign in" : "Create an account"}
          </Link>
        </p>
      </section>
    </main>
  );
}

type FieldProps = {
  id: string;
  label: string;
  type: string;
  value: string;
  error?: string;
  hint?: string;
  autoComplete: string;
  onChange: (value: string) => void;
};

function Field({
  id,
  label,
  type,
  value,
  error,
  hint,
  autoComplete,
  onChange,
}: FieldProps) {
  const descriptionId = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <input
        className="input"
        id={id}
        name={id}
        type={type}
        value={value}
        autoComplete={autoComplete}
        aria-invalid={Boolean(error)}
        aria-describedby={descriptionId}
        onChange={(event) => onChange(event.target.value)}
      />
      {error ? (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="field-hint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
