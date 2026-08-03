"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth-provider";

function isActive(pathname: string, href: string): boolean {
  if (pathname === href) return true;
  if (href === "/lab") return false;
  return pathname.startsWith(`${href}/`);
}

export function LabShell({ children }: { children: React.ReactNode }) {
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
    { href: "/lab", label: "Overview" },
    { href: "/lab/creators", label: "Creators" },
    ...(isAdmin
      ? [{ href: "/lab/brand-analysis", label: "Brand analysis" }]
      : []),
  ];

  return (
    <div className="lab-shell">
      <aside className="lab-sidebar" aria-label="Lab navigation">
        <Link href="/lab" className="brand">
          <span className="brand-mark">I</span>
          <span>Invo Lab</span>
        </Link>
        <nav aria-label="Lab tools" className="lab-nav">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={isActive(pathname, link.href) ? "page" : undefined}
              className={`lab-nav-link ${
                isActive(pathname, link.href) ? "lab-nav-link-active" : ""
              }`}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="mt-auto border-t border-slate-200/60 p-4">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-800">
              {user.email}
            </p>
            <p className="text-xs capitalize text-slate-500">{user.role}</p>
          </div>
          <button
            type="button"
            className="button button-secondary mt-3 w-full"
            onClick={() => void signOut()}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="lab-main">
        <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
          {children}
        </div>
      </main>
    </div>
  );
}
