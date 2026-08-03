import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";
import { ProfileAnalysis } from "@/components/profile-analysis";

export const metadata: Metadata = { title: "My profile" };

export default function ProfilePage() {
  return (
    <AppShell>
      <ProfileAnalysis />
    </AppShell>
  );
}
