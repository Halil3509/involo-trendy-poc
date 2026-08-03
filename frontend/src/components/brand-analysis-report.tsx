"use client";

import { useMemo, useState, type ReactNode } from "react";

import type { BrandAnalysisReport } from "@/lib/types";

type BrandAnalysisReportProps = {
  report: BrandAnalysisReport | null;
  copied: boolean;
  onCopy: () => void;
  onExportPdf: () => void;
  pdfStatus: "idle" | "loading" | "success" | "error";
  pdfError?: string;
};

const pdfButtonLabel: Record<BrandAnalysisReportProps["pdfStatus"], string> = {
  idle: "PDF İndir",
  loading: "PDF Hazırlanıyor...",
  success: "PDF İndirildi",
  error: "PDF Hatası",
};

function safeUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function inlineText(value: string): ReactNode {
  const tokens = value.split(/(\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_|`[^`]+`|\[[^\]]+\]\([^\)]+\))/g);
  return tokens.map((token, index) => {
    if (token.startsWith("**") && token.endsWith("**")) {
      return <strong key={index}>{inlineText(token.slice(2, -2))}</strong>;
    }
    if ((token.startsWith("*") && token.endsWith("*")) || (token.startsWith("_") && token.endsWith("_"))) {
      return <em key={index}>{inlineText(token.slice(1, -1))}</em>;
    }
    if (token.startsWith("`") && token.endsWith("`")) {
      return <code key={index} className="rounded bg-slate-100 px-1 py-0.5 text-[0.9em]">{token.slice(1, -1)}</code>;
    }
    const link = token.match(/^\[([^\]]+)\]\(([^\)]+)\)$/);
    if (link) {
      const href = safeUrl(link[2]);
      return href ? <a key={index} href={href} target="_blank" rel="noreferrer" className="text-indigo-700 underline">{link[1]}</a> : token;
    }
    return <span key={index}>{token}</span>;
  });
}

type ListNode = {
  content: string;
  children: ListNode[];
  ordered: boolean;
};

function normalizeMarkdown(markdown: string): string {
  return markdown
    .replace(/\r\n/g, "\n")
    .replace(/^(#{1,6})\s+.+$/gm, "\n\n$&\n\n");
}

function parseListLine(line: string): { indent: number; ordered: boolean; content: string } | null {
  const match = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
  if (!match) return null;
  return {
    indent: match[1].length,
    ordered: /^\d+\./.test(match[2]),
    content: match[3].trim(),
  };
}

function isListBlock(lines: string[]): boolean {
  let itemCount = 0;
  for (const line of lines) {
    if (parseListLine(line)) {
      itemCount++;
      continue;
    }
    if (/^\s+/.test(line) && !/^(#{1,6})\s+/.test(line.trim()) && !line.trim().startsWith("|") && !line.trim().startsWith(">") && !line.trim().startsWith("![")) {
      continue;
    }
    return false;
  }
  return itemCount > 0;
}

function parseListNodes(lines: string[]): ListNode[] {
  const nodes: ListNode[] = [];
  const stack: { indent: number; node: ListNode }[] = [];
  let current: ListNode | null = null;
  for (const raw of lines) {
    const parsed = parseListLine(raw);
    if (!parsed) {
      if (current) {
        current.content += " " + raw.trim();
      }
      continue;
    }
    const node: ListNode = { content: parsed.content, children: [], ordered: parsed.ordered };
    while (stack.length > 0 && parsed.indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }
    if (stack.length === 0) {
      nodes.push(node);
    } else {
      stack[stack.length - 1].node.children.push(node);
    }
    stack.push({ indent: parsed.indent, node });
    current = node;
  }
  return nodes;
}

function ListTree({ nodes, baseKey }: { nodes: ListNode[]; baseKey: string | number }) {
  if (nodes.length === 0) return null;
  const ordered = nodes.every((node) => node.ordered);
  const Tag = ordered ? "ol" : "ul";
  const className = ordered ? "list-decimal space-y-1 pl-6" : "list-disc space-y-1 pl-6";
  return (
    <Tag className={className}>
      {nodes.map((node, index) => (
        <li key={`${baseKey}-${index}`}>
          {inlineText(node.content)}
          {node.children.length > 0 && <ListTree nodes={node.children} baseKey={`${baseKey}-${index}-c`} />}
        </li>
      ))}
    </Tag>
  );
}

function renderBlock(block: string, key: string | number): ReactNode {
  const lines = block.split("\n").filter(Boolean);
  const heading = lines[0]?.match(/^(#{1,6})\s+(.+)$/);
  if (heading) {
    const level = heading[1].length;
    const Tag = level === 1 ? "h1" : level === 2 ? "h2" : level === 3 ? "h3" : level === 4 ? "h4" : level === 5 ? "h5" : "h6";
    return <Tag key={key} className={level === 1 ? "text-2xl" : "text-lg"}>{inlineText(heading[2])}</Tag>;
  }
  if (isListBlock(lines)) {
    return <ListTree key={key} nodes={parseListNodes(lines)} baseKey={key} />;
  }
  if (lines.length >= 2 && lines[0].includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[1])) {
    const cells = (line: string) => line.split("|").map((cell) => cell.trim()).filter(Boolean);
    return (
      <div key={key} className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead>
            <tr>{cells(lines[0]).map((cell, index) => <th key={`h-${index}`} className="border-b border-slate-200 px-3 py-2 font-semibold">{inlineText(cell)}</th>)}</tr>
          </thead>
          <tbody>{lines.slice(2).map((line, rowIndex) => <tr key={`r-${rowIndex}`}>{cells(line).map((cell, cellIndex) => <td key={`c-${rowIndex}-${cellIndex}`} className="border-b border-slate-100 px-3 py-2 align-top">{inlineText(cell)}</td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  }
  if (lines.every((line) => line.startsWith(">"))) {
    return <blockquote key={key} className="border-l-4 border-indigo-200 pl-4 italic text-slate-600">{inlineText(lines.map((line) => line.replace(/^>\s?/, "")).join(" "))}</blockquote>;
  }
  if (lines.length === 1) {
    const image = lines[0].match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
    if (image) {
      const [, alt, src] = image;
      const url = safeUrl(src);
      if (url) {
        return <img key={key} src={url} alt={alt} className="max-w-full rounded-lg my-2" />;
      }
      return <p key={key}>{inlineText(lines[0])}</p>;
    }
  }
  return <p key={key}>{inlineText(lines.join(" "))}</p>;
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  const blocks = useMemo(() => {
    const normalized = normalizeMarkdown(markdown);
    return normalized.split(/\n{2,}/).filter((b) => b.trim().length > 0);
  }, [markdown]);
  return (
    <article className="prose prose-slate max-w-none text-sm leading-7">
      {blocks.map((block, index) => renderBlock(block, index))}
    </article>
  );
}

function ConfidenceBadge({ level }: { level: string }) {
  const tone: Record<string, string> = {
    low: "bg-amber-100 text-amber-800",
    medium: "bg-blue-100 text-blue-800",
    high: "bg-emerald-100 text-emerald-800",
  };
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${tone[level] ?? tone.low}`}
      aria-label={`güven: ${level}`}
    >
      {level}
    </span>
  );
}

function EvidenceChainCard({ chain }: { chain: import("@/lib/types").EvidenceChain }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <h4 className="text-sm font-semibold text-slate-900">{chain.observation}</h4>
        <ConfidenceBadge level={chain.confidence} />
      </div>
      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="font-medium text-slate-700">Semantik anlam</dt>
          <dd className="text-slate-600">{chain.semantic_meaning}</dd>
        </div>
        <div>
          <dt className="font-medium text-slate-700">Beğenilme nedeni</dt>
          <dd className="text-slate-600">{chain.preference_hypothesis}</dd>
        </div>
        <div>
          <dt className="font-medium text-slate-700">Uyarlanabilir prensip</dt>
          <dd className="text-slate-600">{chain.adaptable_principle}</dd>
        </div>
        <div>
          <dt className="font-medium text-slate-700">Stratejik karar</dt>
          <dd className="text-slate-900 font-medium">{chain.strategic_decision}</dd>
        </div>
        {chain.alternative_explanation && (
          <div>
            <dt className="font-medium text-slate-700">Alternatif açıklama</dt>
            <dd className="text-slate-500">{chain.alternative_explanation}</dd>
          </div>
        )}
      </dl>
      {chain.evidence.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-slate-100 pt-3">
          {chain.evidence.map((ev, idx) => (
            <li key={idx} className="text-xs text-slate-500">
              <span className="font-medium">{ev.shortcode}</span> ({ev.field}): {ev.why_supports}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MetricRow({ metric }: { metric: import("@/lib/types").MetricObservation }) {
  return (
    <div className="flex items-start justify-between gap-3 py-2 text-sm">
      <div>
        <span className="font-medium text-slate-700">{metric.label}</span>
        <span className="ml-2 text-xs text-slate-400">({metric.basis})</span>
        {metric.note && <p className="text-xs text-slate-500">{metric.note}</p>}
      </div>
      <div className="flex items-center gap-2">
        <span className="font-semibold text-slate-900">{metric.value}</span>
        <ConfidenceBadge level={metric.confidence} />
        {!metric.comparable && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">karşılaştırılamaz</span>
        )}
      </div>
    </div>
  );
}

function StructuredReport({ report }: { report: import("@/lib/types").BrandAnalysisReport }) {
  const brief = report.strategic_brief;
  if (!brief) return <MarkdownPreview markdown={report.markdown_text} />;

  return (
    <article className="space-y-8">
      <section aria-labelledby="executive-heading">
        <h3 id="executive-heading" className="section-title">Yönetici Özeti</h3>
        <p className="mt-2 text-sm leading-7 text-slate-700">{brief.executive_answer}</p>
      </section>

      {brief.success_dna && (
        <section aria-labelledby="success-dna-heading">
          <h3 id="success-dna-heading" className="section-title">Marka Başarısı DNA&apos;sı</h3>
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
            <dl className="space-y-2">
              <div><dt className="font-medium text-slate-700">Arzu</dt><dd>{brief.success_dna.desire}</dd></div>
              <div><dt className="font-medium text-slate-700">Kanıt</dt><dd>{brief.success_dna.proof}</dd></div>
              <div><dt className="font-medium text-slate-700">Yaşam Tarzı</dt><dd>{brief.success_dna.lifestyle}</dd></div>
            </dl>
          </div>
        </section>
      )}

      <section aria-labelledby="brand-world-heading">
        <h3 id="brand-world-heading" className="section-title">Marka Dünyası</h3>
        <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
          <dl className="space-y-2">
            <div><dt className="font-medium text-slate-700">Hissi</dt><dd>{brief.brand_world.emotional_effect}</dd></div>
            <div><dt className="font-medium text-slate-700">Vaat</dt><dd>{brief.brand_world.brand_promise}</dd></div>
            <div><dt className="font-medium text-slate-700">Persona</dt><dd>{brief.brand_world.persona}</dd></div>
            <div><dt className="font-medium text-slate-700">Yaşam tarzı</dt><dd>{brief.brand_world.lifestyle_context}</dd></div>
            <div><dt className="font-medium text-slate-700">Premium mekanizması</dt><dd>{brief.brand_world.premium_mechanism}</dd></div>
          </dl>
          {brief.brand_world.visual_codes.length > 0 && (
            <p className="mt-2"><span className="font-medium">Görsel kodlar:</span> {brief.brand_world.visual_codes.join(", ")}</p>
          )}
          {brief.brand_world.verbal_codes.length > 0 && (
            <p className="mt-1"><span className="font-medium">Sözel kodlar:</span> {brief.brand_world.verbal_codes.join(", ")}</p>
          )}
          <p className="mt-2"><ConfidenceBadge level={brief.brand_world.confidence} /></p>
        </div>
      </section>

      {brief.content_series && brief.content_series.length > 0 && (
        <section aria-labelledby="content-series-heading">
          <h3 id="content-series-heading" className="section-title">İçerik Serisi Mekanikleri</h3>
          <ul className="mt-3 grid gap-3">
            {brief.content_series.map((series, idx) => (
              <li key={idx} className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
                <h4 className="font-semibold text-slate-900">{series.mechanic_name} (%{series.percentage_of_sample})</h4>
                <p className="mt-1"><span className="font-medium text-slate-700">Psikolojik işlev:</span> {series.psychological_function}</p>
                <p className="mt-1"><span className="font-medium text-slate-700">Uygulama formülü:</span> {series.execution_formula}</p>
                {series.content_jobs.length > 0 && (
                  <p className="mt-1 text-slate-500">Görevler: {series.content_jobs.join(", ")}</p>
                )}
                {series.sample_shortcodes.length > 0 && (
                  <p className="mt-1 text-slate-500">Örnekler: {series.sample_shortcodes.join(", ")}</p>
                )}
                <p className="mt-2"><ConfidenceBadge level={series.confidence} /></p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {brief.visual_dna && (
        <section aria-labelledby="visual-dna-heading">
          <h3 id="visual-dna-heading" className="section-title">Görsel Kimlik (Visual DNA)</h3>
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
            {brief.visual_dna.color_palette.length > 0 && (
              <div className="mb-2">
                <span className="font-medium text-slate-700">Renk paleti: </span>
                <div className="mt-1 flex flex-wrap gap-2">
                  {brief.visual_dna.color_palette.map((color, idx) => (
                    <span key={idx} className="rounded-full bg-white px-3 py-1 text-xs border border-slate-200">{color}</span>
                  ))}
                </div>
              </div>
            )}
            {brief.visual_dna.lighting_recipe && (
              <p><span className="font-medium text-slate-700">Işık reçetesi:</span> {brief.visual_dna.lighting_recipe}</p>
            )}
            {brief.visual_dna.texture_signatures.length > 0 && (
              <p className="mt-1"><span className="font-medium text-slate-700">Doku imzaları:</span> {brief.visual_dna.texture_signatures.join(", ")}</p>
            )}
            {brief.visual_dna.shooting_angles.length > 0 && (
              <p className="mt-1"><span className="font-medium text-slate-700">Çekim açıları:</span> {brief.visual_dna.shooting_angles.join(", ")}</p>
            )}
            {brief.visual_dna.aesthetic_style && (
              <p className="mt-1"><span className="font-medium text-slate-700">Estetik stil:</span> {brief.visual_dna.aesthetic_style}</p>
            )}
            {brief.visual_dna.avoided_visual_elements.length > 0 && (
              <p className="mt-1 text-amber-700"><span className="font-medium">Kaçınılan görsel unsurlar:</span> {brief.visual_dna.avoided_visual_elements.join(", ")}</p>
            )}
            <p className="mt-2"><ConfidenceBadge level={brief.visual_dna.confidence} /></p>
          </div>
        </section>
      )}

      {brief.persona_profile && (
        <section aria-labelledby="persona-heading">
          <h3 id="persona-heading" className="section-title">Hedef Kitle Profili (Persona)</h3>
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
            <dl className="space-y-2">
              {brief.persona_profile.age_range && <div><dt className="font-medium text-slate-700">Yaş aralığı</dt><dd>{brief.persona_profile.age_range}</dd></div>}
              {brief.persona_profile.lifestyle_descriptor && <div><dt className="font-medium text-slate-700">Yaşam tarzı</dt><dd>{brief.persona_profile.lifestyle_descriptor}</dd></div>}
              {brief.persona_profile.aspiration && <div><dt className="font-medium text-slate-700">Aspirasyon</dt><dd>{brief.persona_profile.aspiration}</dd></div>}
              {brief.persona_profile.psychological_trigger && <div><dt className="font-medium text-slate-700">Psikolojik tetikleyici</dt><dd>{brief.persona_profile.psychological_trigger}</dd></div>}
            </dl>
            {brief.persona_profile.trigger_phrases.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {brief.persona_profile.trigger_phrases.map((phrase, idx) => (
                  <span key={idx} className="rounded bg-indigo-100 px-2 py-0.5 text-xs text-indigo-800">{phrase}</span>
                ))}
              </div>
            )}
            <p className="mt-2"><ConfidenceBadge level={brief.persona_profile.confidence} /></p>
          </div>
        </section>
      )}

      {brief.carousel_anatomy && (
        <section aria-labelledby="carousel-heading">
          <h3 id="carousel-heading" className="section-title">Carousel Anatomisi</h3>
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
            {brief.carousel_anatomy.hook_pattern && (
              <p><span className="font-medium text-slate-700">Hook deseni:</span> {brief.carousel_anatomy.hook_pattern}</p>
            )}
            {brief.carousel_anatomy.avg_slide_count > 0 && (
              <p className="mt-1"><span className="font-medium text-slate-700">Ortalama slayt sayısı:</span> {brief.carousel_anatomy.avg_slide_count}</p>
            )}
            {brief.carousel_anatomy.slide_roles.length > 0 && (
              <ol className="mt-2 space-y-1">
                {brief.carousel_anatomy.slide_roles.map((slide) => (
                  <li key={slide.slide_number} className="flex items-start gap-2">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-xs font-bold text-white">{slide.slide_number}</span>
                    <span><span className="font-medium">{slide.role}</span>{slide.content_pattern && <span className="text-slate-500"> — {slide.content_pattern}</span>}</span>
                  </li>
                ))}
              </ol>
            )}
            {brief.carousel_anatomy.cta_pattern && (
              <p className="mt-2"><span className="font-medium text-slate-700">CTA deseni:</span> {brief.carousel_anatomy.cta_pattern}</p>
            )}
            <p className="mt-2"><ConfidenceBadge level={brief.carousel_anatomy.confidence} /></p>
          </div>
        </section>
      )}

      {brief.weekly_content_calendar && brief.weekly_content_calendar.entries.length > 0 && (
        <section aria-labelledby="calendar-heading">
          <h3 id="calendar-heading" className="section-title">Haftalık İçerik Takvimi</h3>
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                  <th className="py-2 pr-3 font-medium">Gün</th>
                  <th className="py-2 pr-3 font-medium">İçerik Kümesi</th>
                  <th className="py-2 pr-3 font-medium">Format</th>
                  <th className="py-2 pr-3 font-medium">Hook</th>
                  <th className="py-2 pr-3 font-medium">Slayt</th>
                  <th className="py-2 font-medium">CTA</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {brief.weekly_content_calendar.entries.map((entry, idx) => (
                  <tr key={idx} className="text-slate-700">
                    <td className="py-2 pr-3 font-medium">{entry.day}</td>
                    <td className="py-2 pr-3">{entry.content_cluster}</td>
                    <td className="py-2 pr-3">{entry.format}</td>
                    <td className="py-2 pr-3 text-xs">{entry.hook_template}</td>
                    <td className="py-2 pr-3">{entry.slide_count ?? "-"}</td>
                    <td className="py-2 text-xs">{entry.cta_template}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {brief.weekly_content_calendar.weekly_cadence_note && (
            <p className="mt-2 text-xs text-slate-500">{brief.weekly_content_calendar.weekly_cadence_note}</p>
          )}
          <p className="mt-1"><ConfidenceBadge level={brief.weekly_content_calendar.confidence} /></p>
        </section>
      )}

      {brief.production_brief && brief.production_brief.length > 0 && (
        <section aria-labelledby="production-heading">
          <h3 id="production-heading" className="section-title">Çekim Brief&lsquo;i (Production Brief)</h3>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-slate-700">
            {brief.production_brief.map((item, idx) => (
              <li key={idx}>{item}</li>
            ))}
          </ol>
        </section>
      )}

      {brief.preference_hypotheses.length > 0 && (
        <section aria-labelledby="preference-heading">
          <h3 id="preference-heading" className="section-title">Hizmet Verilen Marka Neden Beğenebilir?</h3>
          <div className="mt-3 grid gap-4">
            {brief.preference_hypotheses.map((chain, idx) => (
              <EvidenceChainCard key={chain.chain_id || idx} chain={chain} />
            ))}
          </div>
        </section>
      )}

      {brief.evidence_chains.length > 0 && (
        <section aria-labelledby="evidence-heading">
          <h3 id="evidence-heading" className="section-title">Kanıt Zincirleri</h3>
          <div className="mt-3 grid gap-4">
            {brief.evidence_chains.map((chain, idx) => (
              <EvidenceChainCard key={chain.chain_id || idx} chain={chain} />
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="recipe-heading">
        <h3 id="recipe-heading" className="section-title">İçerik Reçetesi</h3>
        <div className="mt-3 text-sm text-slate-700">
          <p><span className="font-medium">Kapsam:</span> {brief.content_recipe.coverage_label} {brief.content_recipe.observed_window_days !== null ? `(${brief.content_recipe.observed_window_days} gün)` : ""} <ConfidenceBadge level={brief.content_recipe.confidence} /></p>
          <p className="mt-1"><span className="font-medium">Yayın sıklığı:</span> {brief.content_recipe.cadence_estimate} {brief.content_recipe.posts_per_week_estimate !== null ? `(~${brief.content_recipe.posts_per_week_estimate} / hafta)` : ""} <ConfidenceBadge level={brief.content_recipe.cadence_confidence} /></p>
          {brief.content_recipe.formats.length > 0 && (
            <ul className="mt-3 grid gap-2">
              {brief.content_recipe.formats.map((fmt) => (
                <li key={fmt.format} className="rounded border border-slate-200 bg-white p-3">
                  <span className="font-semibold">{fmt.format}</span>: {fmt.count} adet (%{fmt.percentage}) — {fmt.role_in_brand_world}. {" "}
                  {fmt.content_jobs.length > 0 && <span className="text-slate-500">Görevler: {fmt.content_jobs.join(", ")}</span>}
                  <span className="ml-2"><ConfidenceBadge level={fmt.confidence} /></span>
                </li>
              ))}
            </ul>
          )}
          {brief.content_recipe.anomaly_count > 0 && (
            <p className="mt-2 text-amber-700">{brief.content_recipe.anomaly_count} olağandışı gönderi ayrıldı. {brief.content_recipe.anomaly_note}</p>
          )}
        </div>
      </section>

      <section aria-labelledby="performance-heading">
        <h3 id="performance-heading" className="section-title">Performans Kanıtları</h3>
        <div className="mt-3 text-sm">
          {brief.performance_summary.data_quality_notes.length > 0 && (
            <ul className="mb-3 list-disc space-y-1 rounded border border-amber-200 bg-amber-50 p-3 pl-5 text-amber-800">
              {brief.performance_summary.data_quality_notes.map((note, idx) => (
                <li key={idx}>{note}</li>
              ))}
            </ul>
          )}
          {brief.performance_summary.organic_metrics.length > 0 && (
            <div className="divide-y divide-slate-100">
              {brief.performance_summary.organic_metrics.map((m, idx) => (
                <MetricRow key={idx} metric={m} />
              ))}
            </div>
          )}
          {brief.performance_summary.anomaly_metrics.length > 0 && (
            <div className="mt-4 divide-y divide-slate-100 border-t border-slate-200">
              <p className="py-2 font-medium text-slate-700">Olağandışı performans</p>
              {brief.performance_summary.anomaly_metrics.map((m, idx) => (
                <MetricRow key={idx} metric={m} />
              ))}
            </div>
          )}
          {brief.performance_summary.valid_rate_comparisons.length > 0 && (
            <ul className="mt-3 list-disc pl-5 text-emerald-700">
              {brief.performance_summary.valid_rate_comparisons.map((c, idx) => <li key={idx}>{c}</li>)}
            </ul>
          )}
          {brief.performance_summary.invalid_rate_comparisons.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-red-700">
              {brief.performance_summary.invalid_rate_comparisons.map((c, idx) => <li key={idx}>{c}</li>)}
            </ul>
          )}
        </div>
      </section>

      {brief.limitations.length > 0 && (
        <section aria-labelledby="limitations-heading">
          <h3 id="limitations-heading" className="section-title">Çıkarılamayacak Sonuçlar</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">
            {brief.limitations.map((limit, idx) => <li key={idx}>{limit}</li>)}
          </ul>
        </section>
      )}

      {brief.decisions.length > 0 && (
        <section aria-labelledby="decisions-heading">
          <h3 id="decisions-heading" className="section-title">Stratejik Kararlar ({brief.decisions.length})</h3>
          <ol className="mt-3 space-y-4">
            {brief.decisions.map((decision, idx) => (
              <li key={idx} className="rounded-lg border border-indigo-100 bg-indigo-50 p-4">
                <h4 className="text-sm font-semibold text-indigo-900">{idx + 1}. {decision.decision}</h4>
                <p className="mt-1 text-sm text-slate-700">{decision.rationale}</p>
                <dl className="mt-2 grid gap-1 text-xs text-slate-600">
                  <div><dt className="font-medium">Guardrail:</dt><dd>{decision.guardrail}</dd></div>
                  <div><dt className="font-medium">İlk eylem:</dt><dd>{decision.first_action}</dd></div>
                  <div><dt className="font-medium">Başarı sinyali:</dt><dd>{decision.success_signal}</dd></div>
                </dl>
                <span className="mt-2 inline-block"><ConfidenceBadge level={decision.confidence} /></span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

export function BrandAnalysisReport({
  report,
  copied,
  onCopy,
  onExportPdf,
  pdfStatus,
  pdfError,
}: BrandAnalysisReportProps) {
  const [rawMarkdown, setRawMarkdown] = useState(false);
  if (!report) return null;
  return (
    <section className="card" aria-labelledby="brand-report-heading">
      <div className="card-header flex flex-wrap items-center justify-between gap-3">
        <h2 id="brand-report-heading" className="section-title">Rapor</h2>
        <div className="flex items-center gap-2">
          {report.strategic_brief && (
            <button
              type="button"
              className="button button-secondary"
              onClick={() => setRawMarkdown((v: boolean) => !v)}
            >
              {rawMarkdown ? "Yapılandırılmış" : "Markdown"}
            </button>
          )}
          <button
            type="button"
            className="button button-secondary"
            onClick={onExportPdf}
            disabled={pdfStatus === "loading"}
            aria-label="PDF olarak indir"
            title={pdfError}
          >
            {pdfButtonLabel[pdfStatus]}
          </button>
          <button type="button" className="button button-secondary" onClick={onCopy} disabled={copied}>{copied ? "Kopyalandı" : "Kopyala"}</button>
        </div>
      </div>
      <div className="p-4 sm:p-5">
        {rawMarkdown || !report.strategic_brief ? (
          <MarkdownPreview markdown={report.markdown_text} />
        ) : (
          <StructuredReport report={report} />
        )}
        {report.report_s3_key && <p className="mt-6 text-xs text-slate-400">Rapor arşivi: {report.report_s3_key}</p>}
      </div>
    </section>
  );
}
