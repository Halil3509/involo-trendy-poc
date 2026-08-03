import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { BrandAnalysisReport } from "@/components/brand-analysis-report";
import type { BrandAnalysisReport as BrandAnalysisReportType } from "@/lib/types";

const mockReport: BrandAnalysisReportType = {
  schema_version: "brand-analysis-report-v1",
  job_id: "job-1",
  markdown_text: "# Marka Raporu\n\n- Madde 1\n- Madde 2",
  report_s3_key: "reports/brand/job-1/report.md",
  pdf_s3_key: null,
};

const mockStrategicBrief: BrandAnalysisReportType = {
  ...mockReport,
  markdown_text: "# @markaadi",
  strategic_brief: {
    executive_answer: "Executive summary",
    success_dna: {
      desire: "Desire",
      proof: "Proof",
      lifestyle: "Lifestyle",
    },
    brand_world: {
      emotional_effect: "effect",
      brand_promise: "promise",
      persona: "persona",
      visual_codes: ["green", "minimal"],
      verbal_codes: ["science"],
      lifestyle_context: "lifestyle",
      premium_mechanism: "mechanism",
      avoided_elements: ["cheap"],
      confidence: "medium",
    },
    preference_hypotheses: [
      {
        chain_id: "chain-1",
        observation: "Observation",
        semantic_meaning: "Meaning",
        preference_hypothesis: "Preference",
        adaptable_principle: "Principle",
        strategic_decision: "Decision",
        evidence: [],
        alternative_explanation: "Alternative",
        confidence: "medium",
      },
    ],
    evidence_chains: [
      {
        chain_id: "chain-1",
        observation: "Observation",
        semantic_meaning: "Meaning",
        preference_hypothesis: "Preference",
        adaptable_principle: "Principle",
        strategic_decision: "Decision",
        evidence: [
          {
            shortcode: "sc1",
            permalink: "https://example.com/p/sc1",
            field: "caption",
            excerpt: "excerpt",
            why_supports: "supports",
            confidence: "medium",
          },
        ],
        alternative_explanation: "Alternative",
        confidence: "medium",
      },
    ],
    content_recipe: {
      observed_window_days: 14,
      coverage_label: "coverage",
      cadence_estimate: "2 günde bir",
      posts_per_week_estimate: 3.5,
      cadence_confidence: "medium",
      formats: [
        {
          format: "IMAGE",
          count: 3,
          percentage: 60,
          role_in_brand_world: "showcase",
          content_jobs: ["create_desire"],
          confidence: "medium",
        },
      ],
      content_jobs: [["create_desire", "role"]],
      anomaly_count: 1,
      anomaly_note: "giveaway excluded",
      confidence: "medium",
    },
    content_series: [
      {
        mechanic_name: "Texture Series",
        base_category_type: "sensory_proof",
        observed_frequency: 3,
        percentage_of_sample: 60,
        psychological_function: "Makes the product promise credible through visible texture.",
        execution_formula: "Show a close-up texture swatch followed by the product in a lifestyle scene.",
        content_jobs: ["create_desire"],
        sample_shortcodes: ["sc1"],
        evidence_excerpt: "Close-up cream swatch on bare skin.",
        confidence: "medium",
      },
    ],
    performance_summary: {
      organic_metrics: [
        { label: "Toplam beğeni", value: 100, basis: "raw_total", comparable: false, confidence: "medium" },
      ],
      anomaly_metrics: [
        { label: "Toplam beğeni", value: 500, basis: "raw_total", comparable: false, confidence: "low" },
      ],
      data_quality_notes: ["View verisi yok."],
      valid_rate_comparisons: [],
      invalid_rate_comparisons: ["Format kıyası yapılamaz."],
    },
    limitations: ["Örneklem küçük."],
    decisions: [
      {
        decision: "Decision 1",
        rationale: "Rationale",
        evidence_chain_ids: ["chain-1"],
        guardrail: "Guardrail",
        first_action: "Action",
        success_signal: "Signal",
        confidence: "medium",
      },
      {
        decision: "Decision 2",
        rationale: "Rationale 2",
        evidence_chain_ids: ["chain-1"],
        guardrail: "Guardrail",
        first_action: "Action",
        success_signal: "Signal",
        confidence: "medium",
      },
      {
        decision: "Decision 3",
        rationale: "Rationale 3",
        evidence_chain_ids: ["chain-1"],
        guardrail: "Guardrail",
        first_action: "Action",
        success_signal: "Signal",
        confidence: "medium",
      },
    ],
  },
};

const defaultHandlers = {
  onCopy: () => {},
  onExportPdf: () => {},
  pdfStatus: "idle" as const,
};

describe("BrandAnalysisReport", () => {
  it("renders nothing when report is null", () => {
    const { container } = render(
      <BrandAnalysisReport
        report={null}
        copied={false}
        {...defaultHandlers}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders markdown content and copy and pdf buttons", () => {
    render(<BrandAnalysisReport report={mockReport} copied={false} {...defaultHandlers} />);

    expect(screen.getByRole("heading", { name: "Rapor" })).toBeInTheDocument();
    expect(screen.getByText(/Marka Raporu/)).toBeInTheDocument();
    expect(screen.getByText(/Madde 1/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kopyala" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PDF olarak indir" })).toBeInTheDocument();
    expect(screen.getByText(/reports\/brand\/job-1\/report.md/)).toBeInTheDocument();
  });

  it("shows copied state after copy is triggered", () => {
    render(<BrandAnalysisReport report={mockReport} copied {...defaultHandlers} />);

    expect(screen.getByRole("button", { name: "Kopyalandı" })).toBeInTheDocument();
  });

  it("shows loading state on the pdf button", () => {
    render(
      <BrandAnalysisReport
        report={mockReport}
        copied={false}
        onCopy={defaultHandlers.onCopy}
        onExportPdf={defaultHandlers.onExportPdf}
        pdfStatus="loading"
      />,
    );

    const button = screen.getByRole("button", { name: "PDF olarak indir" });
    expect(button).toBeDisabled();
    expect(screen.getByText("PDF Hazırlanıyor...")).toBeInTheDocument();
  });

  it("shows success state on the pdf button", () => {
    render(
      <BrandAnalysisReport
        report={mockReport}
        copied={false}
        onCopy={defaultHandlers.onCopy}
        onExportPdf={defaultHandlers.onExportPdf}
        pdfStatus="success"
      />,
    );

    expect(screen.getByText("PDF İndirildi")).toBeInTheDocument();
  });

  it("calls onExportPdf when the pdf button is clicked", async () => {
    const onExportPdf = vi.fn();
    render(
      <BrandAnalysisReport
        report={mockReport}
        copied={false}
        onCopy={defaultHandlers.onCopy}
        onExportPdf={onExportPdf}
        pdfStatus="idle"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "PDF olarak indir" }));

    await waitFor(() => expect(onExportPdf).toHaveBeenCalled());
  });

  it("calls onCopy when the copy button is clicked", async () => {
    const onCopy = vi.fn();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(
      <BrandAnalysisReport
        report={mockReport}
        copied={false}
        onCopy={onCopy}
        onExportPdf={defaultHandlers.onExportPdf}
        pdfStatus="idle"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Kopyala" }));

    await waitFor(() => expect(onCopy).toHaveBeenCalled());
  });

  it("renders action plan and experiment details from markdown", () => {
    const detailedReport = {
      ...mockReport,
      markdown_text: `## 9. 30/60/90 Günlük Aksiyon Planı

### 30 Gün
- **Eylem:** Haftalık yarışma düzenlemek.
- **Sorumlu Çıktı:** 1 yarışma gönderisi.
- **KPI:** En az 10.000 etkileşim.

### 60 Gün
- **Eylem:** CAROUSEL_ALBUM formatında ürün tanıtımı yapmak.

## 10. Deneyler ve Ölçüm Planı
### Test Edilebilir Deneyler
1. **Yarışma Frekansı Testi**
   - **Hipotez:** Haftalık yarışmalar daha yüksek etkileşim sağlar.
   - **Varyant:** Haftalık vs. aylık yarışmalar.
2. **Format Testi**
   - **Hipotez:** CAROUSEL_ALBUM formatı daha yüksek etkileşim sağlar.

## Kanıt Ekleri
| Kanıt | Format | Beğeni |
|-------|--------|--------|
| abc   | IMAGE  | 100    |

*abc123 — 100 beğeni — [gönderi](https://example.com/p/abc)*`,
    };
    render(<BrandAnalysisReport report={detailedReport} copied={false} {...defaultHandlers} />);

    expect(screen.getByText(/Haftalık yarışma düzenlemek/)).toBeInTheDocument();
    expect(screen.getByText(/1 yarışma gönderisi/)).toBeInTheDocument();
    expect(screen.getByText(/En az 10\.000 etkileşim/)).toBeInTheDocument();
    expect(screen.getByText(/CAROUSEL_ALBUM formatında ürün tanıtımı yapmak/)).toBeInTheDocument();
    expect(screen.getByText(/Yarışma Frekansı Testi/)).toBeInTheDocument();
    expect(screen.getByText(/Haftalık yarışmalar daha yüksek etkileşim sağlar/)).toBeInTheDocument();
    expect(screen.getByText(/Haftalık vs\. aylık yarışmalar/)).toBeInTheDocument();
    expect(screen.getByText(/Format Testi/)).toBeInTheDocument();
    expect(screen.getByText(/CAROUSEL_ALBUM formatı daha yüksek etkileşim sağlar/)).toBeInTheDocument();
    expect(screen.getByText("IMAGE")).toBeInTheDocument();
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
    expect(screen.queryByText(/\*abc123/)).not.toBeInTheDocument();
  });

  it("renders markdown images and does not render a video player", () => {
    const reportWithImage = {
      ...mockReport,
      markdown_text: "# Marka Raporu\n\n![Örnek görsel](https://example.com/image.jpg)",
    };
    render(<BrandAnalysisReport report={reportWithImage} copied={false} {...defaultHandlers} />);

    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
  });

  it("renders structured executive answer and strategic decisions", () => {
    render(
      <BrandAnalysisReport
        report={mockStrategicBrief}
        copied={false}
        {...defaultHandlers}
      />,
    );

    expect(screen.getByText("Executive summary")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Marka Dünyası/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Kanıt Zincirleri/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /İçerik Reçetesi/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Stratejik Kararlar/i })).toBeInTheDocument();
    expect(screen.getByText(/1\. Decision 1/)).toBeInTheDocument();
    expect(screen.getByText(/2\. Decision 2/)).toBeInTheDocument();
    expect(screen.getByText(/3\. Decision 3/)).toBeInTheDocument();
  });

  it("shows evidence-chain labels and confidence badges", () => {
    render(
      <BrandAnalysisReport
        report={mockStrategicBrief}
        copied={false}
        {...defaultHandlers}
      />,
    );

    expect(screen.getAllByText("Observation").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Semantik anlam/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Beğenilme nedeni/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("medium").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("sc1")).toBeInTheDocument();
  });

  it("shows data quality notes and non-comparable metric warnings", () => {
    render(
      <BrandAnalysisReport
        report={mockStrategicBrief}
        copied={false}
        {...defaultHandlers}
      />,
    );

    expect(screen.getByText(/View verisi yok/i)).toBeInTheDocument();
    expect(screen.getAllByText(/karşılaştırılamaz/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Format kıyası yapılamaz/i)).toBeInTheDocument();
  });

  it("toggles between structured and markdown views", () => {
    render(
      <BrandAnalysisReport
        report={mockStrategicBrief}
        copied={false}
        {...defaultHandlers}
      />,
    );

    expect(screen.getByText("Executive summary")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    expect(screen.getByText(/@markaadi/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Yapılandırılmış" }));
    expect(screen.getByText("Executive summary")).toBeInTheDocument();
  });
});
