"use client";

import { useCallback } from "react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { normalizeInstagramStatus } from "@/lib/validators";

export function useInstagramStatus() {
  const fetcher = useCallback(() => api.getInstagramStatus(), []);
  const { data, loading, error, refresh } = useAsync(fetcher, {
    normalize: normalizeInstagramStatus,
  });

  return {
    status: data ?? null,
    loading,
    error: error?.message ?? "",
    refresh,
  };
}
