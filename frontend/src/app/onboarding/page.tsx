import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";
import { OnboardingForm } from "@/components/onboarding-form";

export const metadata: Metadata = { title: "Creator setup" };

export default function OnboardingPage() {
  return (
    <AppShell>
      <OnboardingForm />
    </AppShell>
  );
}
