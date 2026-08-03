"use client";

import { AppShell } from "@/components/app-shell";
import { ContentRecommendations } from "@/components/content-recommendations";
import { InstagramProfileCard } from "@/components/instagram-profile-card";
import { useInstagramStatus } from "@/components/use-instagram-status";

export default function RecommendationsPage() {
  const instagram = useInstagramStatus();

  return (
    <AppShell>
      <main className="page-container">
        <div className="mb-8">
          <p className="eyebrow">AI studio</p>
          <h1 className="page-title mt-2">
            Content recommendations<span className="text-indigo-600">.</span>
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Review, understand, and act on your AI-generated content ideas.
          </p>
        </div>

        <ContentRecommendations
          instagramStatus={instagram.status}
          instagramStatusLoading={instagram.loading}
          instagramStatusError={instagram.error}
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
