import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";
import { ScraperAdmin } from "@/components/scraper-admin";

export const metadata: Metadata = { title: "Scraper control" };

export default function ScraperPage() {
  return (
    <AppShell>
      <ScraperAdmin />
    </AppShell>
  );
}
