import { AppShell } from "@/components/app-shell";
import { CreatorDetail } from "@/components/creator-detail";

export default async function CreatorDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <AppShell>
      <main className="page-container">
        <CreatorDetail creatorId={id} />
      </main>
    </AppShell>
  );
}
