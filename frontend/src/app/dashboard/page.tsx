"use client";

import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { ContentRecommendations } from "@/components/content-recommendations";
import { InstagramProfileCard } from "@/components/instagram-profile-card";
import { useInstagramStatus } from "@/components/use-instagram-status";

export default function DashboardPage() {
  const { user } = useAuth();
  const instagram = useInstagramStatus();
  const isAdmin = user?.role?.toLowerCase() === "admin";

  return (
    <AppShell>
      <main className="page-container">
        <div className="mb-8">
          <p className="eyebrow">Overview</p>
          <h1 className="page-title mt-2">
            Good to see you<span className="text-indigo-600">.</span>
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Your workspace is ready for the next content discovery cycle.
          </p>
        </div>

        <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
          <section className="card overflow-hidden">
            <div className="relative bg-gradient-to-br from-indigo-700 via-indigo-600 to-violet-500 p-7 text-white sm:p-9">
              <div className="absolute -right-16 -top-24 h-56 w-56 rounded-full border-[36px] border-white/10" />
              <p className="text-sm font-semibold uppercase tracking-[0.16em] text-indigo-100">
                Content intelligence
              </p>
              <h2 className="mt-4 max-w-lg text-2xl font-semibold leading-snug sm:text-3xl">
                Find the signals worth turning into your next idea.
              </h2>
              <p className="mt-4 max-w-xl text-sm leading-6 text-indigo-100">
                Involo brings discovery, analysis, and recommendations into one
                focused workflow.
              </p>
              {isAdmin && (
                <Link
                  href="/admin/scraper"
                  className="button mt-7 bg-white text-indigo-700 hover:bg-indigo-50"
                >
                  Open scraper control
                  <span aria-hidden="true">→</span>
                </Link>
              )}
            </div>
          </section>

          <section className="card" aria-labelledby="profile-heading">
            <div className="card-header">
              <h2 id="profile-heading" className="section-title">
                Your profile
              </h2>
            </div>
            <div className="p-5 sm:p-6">
              <div className="flex items-center gap-4">
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-lg font-semibold uppercase text-indigo-700">
                  {user?.email?.charAt(0) || "U"}
                </div>
                <div className="min-w-0">
                  <p className="truncate font-medium text-slate-900">
                    {user?.email}
                  </p>
                  <span className="mt-1 inline-flex rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold capitalize text-slate-600">
                    {user?.role ?? "user"}
                  </span>
                </div>
              </div>
              <dl className="mt-6 border-t border-slate-100 pt-5">
                <div className="flex items-center justify-between gap-4 text-sm">
                  <dt className="text-slate-500">Account status</dt>
                  <dd className="flex items-center gap-2 font-medium text-emerald-700">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />
                    Active
                  </dd>
                </div>
              </dl>
            </div>
          </section>
        </div>
        <ContentRecommendations
          instagramStatus={instagram.status}
          instagramStatusLoading={instagram.loading}
          instagramStatusError={instagram.error}
          headerAction={
            <Link
              href="/recommendations"
              className="button button-secondary hidden sm:inline-flex"
            >
              View all recommendations
              <span aria-hidden="true" className="ml-1">→</span>
            </Link>
          }
        />
        <div className="mt-6">
          <InstagramProfileCard
            status={instagram.status}
            loading={instagram.loading}
            statusError={instagram.error}
            refreshStatus={instagram.refresh}
          />
        </div>
      </main>
    </AppShell>
  );
}
