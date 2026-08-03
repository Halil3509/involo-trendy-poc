"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";

import { ApiError, api } from "@/lib/api";
import type { User } from "@/lib/types";

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  refreshUser: () => Promise<User | null>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);
const publicPaths = new Set(["/login", "/register"]);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [onboardingComplete, setOnboardingComplete] = useState<boolean | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  const refreshUser = useCallback(async () => {
    try {
      const profile = await api.refresh();
      if (!profile) {
        setUser(null);
        setOnboardingComplete(null);
        return null;
      }
      setUser(profile);
      if (profile.role === "admin") {
        setOnboardingComplete(true);
      } else {
        const preferences = await api.getPreferences();
        setOnboardingComplete(
          preferences.target_countries.length > 0 &&
            preferences.content_languages.length > 0 &&
            preferences.niches.length > 0 &&
            preferences.goals.length > 0,
        );
      }
      return profile;
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) {
        console.error("Unable to load user profile", error);
      }
      setUser(null);
      setOnboardingComplete(null);
      return null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      refreshUser().finally(() => {
        if (active) setLoading(false);
      });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [refreshUser]);

  useEffect(() => {
    if (loading) return;
    const isPublic = publicPaths.has(pathname);

    if (!user && !isPublic) router.replace("/login");
    const needsOnboarding =
      user?.role !== "admin" && onboardingComplete === false;
    if (user && isPublic) {
      router.replace(needsOnboarding ? "/onboarding" : "/dashboard");
    }
    if (user && needsOnboarding && pathname !== "/onboarding") {
      router.replace("/onboarding");
    }
    if (user && !needsOnboarding && pathname === "/onboarding") {
      router.replace("/dashboard");
    }
    if (
      pathname.startsWith("/admin") &&
      user?.role?.toLowerCase() !== "admin"
    ) {
      router.replace("/dashboard");
    }
  }, [loading, onboardingComplete, pathname, router, user]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
      router.replace("/login");
      router.refresh();
    }
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, refreshUser, signOut }),
    [loading, refreshUser, signOut, user],
  );

  return (
    <AuthContext.Provider value={value}>
      {loading ? (
        <div className="page-center" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span className="sr-only">Loading application</span>
        </div>
      ) : (
        children
      )}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
