"use client";

import { useEffect } from "react";

type ErrorFallbackProps = {
  title?: string;
  message?: string;
  error?: Error;
  reset?: () => void;
  retryLabel?: string;
};

export function ErrorFallback({
  title = "Something went wrong",
  message = "We hit an unexpected issue. Try again, or go back to the dashboard.",
  error,
  reset,
  retryLabel = "Try again",
}: ErrorFallbackProps) {
  useEffect(() => {
    // Log a safe reference only; never leak response bodies, tokens, or stacks to the user.
    if (error && process.env.NODE_ENV === "development") {
      console.error("ErrorFallback caught:", error.name, error.message);
    }
  }, [error]);

  return (
    <div className="page-center min-h-[60vh] flex-col px-4 text-center">
      <div className="alert alert-error max-w-md" role="alert">
        <h1 className="text-base font-semibold">{title}</h1>
        <p className="mt-2 text-sm leading-relaxed">{message}</p>
        {reset && (
          <div className="mt-4 flex justify-center gap-3">
            <button
              type="button"
              className="button button-primary"
              onClick={() => reset()}
            >
              {retryLabel}
            </button>
            <a href="/dashboard" className="button button-secondary">
              Dashboard
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
