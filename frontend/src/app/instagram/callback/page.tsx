"use client";

import { Suspense, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";

function CallbackContent() {
  const searchParams = useSearchParams();
  const status = searchParams.get("instagram") ?? "";
  const message = searchParams.get("message") ?? "";
  const postedRef = useRef(false);

  useEffect(() => {
    if (postedRef.current || typeof window === "undefined") return;
    postedRef.current = true;

    const result =
      status === "connected" || status === "success"
        ? "connected"
        : status === "error"
          ? "error"
          : "unknown";

    if (window.opener) {
      window.opener.postMessage(
        {
          type: "involo:instagram:oauth",
          status: result,
          message: message || undefined,
        },
        window.location.origin,
      );
      window.setTimeout(() => window.close(), 250);
    }
  }, [status, message]);

  if (status === "connected" || status === "success") {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="text-center">
          <h1 className="text-lg font-semibold text-slate-900">
            Instagram connected
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            You can close this window and return to Involo.
          </p>
        </div>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="text-center">
          <h1 className="text-lg font-semibold text-slate-900">
            Connection failed
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            {message || "Instagram connection failed."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="text-center">
        <h1 className="text-lg font-semibold text-slate-900">
          Completing connection...
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          Please wait while we finish connecting your Instagram account.
        </p>
      </div>
    </div>
  );
}

export default function InstagramCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center p-6">
          <div className="text-center">
            <h1 className="text-lg font-semibold text-slate-900">
              Completing connection...
            </h1>
          </div>
        </div>
      }
    >
      <CallbackContent />
    </Suspense>
  );
}
