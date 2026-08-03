"use client";

import { ErrorFallback } from "@/components/error-fallback";

type ErrorPageProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function ErrorPage({ error, reset }: ErrorPageProps) {
  return (
    <ErrorFallback
      title="This page failed to load"
      message="We hit an unexpected problem. Your data is safe; try reloading the page."
      error={error}
      reset={reset}
      retryLabel="Reload page"
    />
  );
}
