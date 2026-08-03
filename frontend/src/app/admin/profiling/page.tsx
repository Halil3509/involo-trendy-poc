import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";
import { ProfilingAdmin } from "@/components/profiling-admin";

export const metadata: Metadata = { title: "Instagram profiling" };

export default function ProfilingPage() {
  return (
    <AppShell>
      <ProfilingAdmin />
    </AppShell>
  );
}
