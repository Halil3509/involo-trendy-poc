import { AppShell } from "@/components/app-shell";
import { BrandAnalysisChat } from "@/components/brand-analysis-chat";

export const metadata = {
  title: "Marka analizi · Involo",
};

export default function BrandAnalysisPage() {
  return (
    <AppShell>
      <main className="page-container">
        <BrandAnalysisChat />
      </main>
    </AppShell>
  );
}
