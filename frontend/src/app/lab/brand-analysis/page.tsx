import type { Metadata } from "next";

import { BrandAnalysisChat } from "@/components/brand-analysis-chat";

export const metadata: Metadata = {
  title: "Brand analysis · Invo Lab",
};

export default function LabBrandAnalysisPage() {
  return <BrandAnalysisChat />;
}
