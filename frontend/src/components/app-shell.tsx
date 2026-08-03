"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth-provider";

function isActive(pathname: string, href: string): boolean {
  if (pathname === href) return true;
  // Keep the admin overview exact so admin sub-routes don't highlight it.
  if (href === "/admin") return false;
  return pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, signOut } = useAuth();
  const pathname = usePathname();

  if (!user) {
    return (
      <div className="page-center" role="status">
        <span className="spinner" />
        <span className="sr-only">Redirecting to sign in</span>
      </div>
    );
  }

  const isAdmin = user.role?.toLowerCase() === "admin";
  const links = [
    { href: "/dashboard", label: "Dashboard" },
    { href: "/recommendations", label: "Recommendations" },
    { href: "/creators", label: "Creators" },
    { href: "/profile", label: "My profile" },
    ...(isAdmin
      ? [
          { href: "/admin", label: "Overview" },
          { href: "/admin/scraper", label: "Scraper control" },
          { href: "/admin/profiling", label: "Profiling" },
          { href: "/admin/brand-analysis", label: "Brand analysis" },
        ]
      : []),
  ];

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="sticky top-0 z-20 border-b border-slate-200/80 bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
          <Link href="/dashboard" className="brand shrink-0">
            <span className="brand-mark">I</span>
            <span>involo</span>
          </Link>
          <nav aria-label="Primary navigation" className="flex items-center gap-1">
            {links.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                aria-current={isActive(pathname, link.href) ? "page" : undefined}
                className={`nav-link ${
                  isActive(pathname, link.href) ? "nav-link-active" : ""
                }`}
              >
                {link.label}
              </Link>
            ))}
          </nav>
          <div className="flex min-w-0 items-center gap-3">
            <div className="hidden min-w-0 text-right sm:block">
              <p className="truncate text-sm font-medium text-slate-800">
                {user.email}
              </p>
              <p className="text-xs capitalize text-slate-500">{user.role}</p>
            </div>
            <button
              type="button"
              className="button button-secondary px-3"
              onClick={() => void signOut()}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      {children}
    </div>
  );
}
