"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const MAX_BACKOFF_MS = 30_000;

export type AsyncState<T> = {
  data: T | undefined;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<T>;
};

type UseAsyncOptions<T> = {
  normalize?: (value: unknown) => T;
  immediate?: boolean;
};

export function useAsync<T>(
  fetcher: () => Promise<T>,
  options: UseAsyncOptions<T> = {},
): AsyncState<T> {
  const { normalize, immediate = true } = options;
  const [data, setData] = useState<T | undefined>(undefined);
  const [loading, setLoading] = useState(immediate);
  const [error, setError] = useState<Error | null>(null);
  const isMountedRef = useRef(true);

  const run = useCallback(async (): Promise<T> => {
    if (isMountedRef.current) {
      setLoading(true);
      setError(null);
    }
    try {
      const raw = await fetcher();
      const normalized = normalize ? normalize(raw as unknown) : (raw as T);
      if (isMountedRef.current) {
        setData(normalized);
      }
      return normalized;
    } catch (caught) {
      const err = caught instanceof Error ? caught : new Error(String(caught));
      if (isMountedRef.current) {
        setError(err);
      }
      throw err;
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  }, [fetcher, normalize]);

  const refresh = useCallback(() => run(), [run]);

  useEffect(() => {
    isMountedRef.current = true;
    if (immediate) {
      run().catch(() => {
        // Errors are already captured in state; do not surface as unhandled rejection.
      });
    }
    return () => {
      isMountedRef.current = false;
    };
  }, [immediate, run]);

  return { data, loading, error, refresh };
}

type UsePollingOptions = {
  interval?: number;
  maxFailures?: number;
  onError?: (error: Error) => void;
};

export function usePolling(
  callback: () => Promise<unknown>,
  active: boolean,
  options: UsePollingOptions = {},
) {
  const { interval = 3_000, maxFailures = 6, onError } = options;
  const failureCountRef = useRef(0);
  const timeoutRef = useRef<number | null>(null);
  const callbackRef = useRef(callback);
  const activeRef = useRef(active);

  useEffect(() => {
    callbackRef.current = callback;
    activeRef.current = active;
  });

  useEffect(() => {
    failureCountRef.current = 0;
    if (!active) return;

    async function tick() {
      try {
        await callbackRef.current();
        failureCountRef.current = 0;
      } catch (caught) {
        failureCountRef.current += 1;
        const err = caught instanceof Error ? caught : new Error(String(caught));
        onError?.(err);
        if (failureCountRef.current >= maxFailures) {
          console.warn("Polling stopped after repeated failures.", err.message);
          return;
        }
      }

      if (!activeRef.current) return;

      const backoff = Math.min(
        interval * 2 ** Math.max(0, failureCountRef.current - 1),
        MAX_BACKOFF_MS,
      );
      timeoutRef.current = window.setTimeout(tick, backoff);
    }

    timeoutRef.current = window.setTimeout(tick, interval);

    return () => {
      activeRef.current = false;
      if (timeoutRef.current) {
        window.clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, [active, interval, maxFailures, onError]);
}
