import { CreatorDetail } from "@/components/creator-detail";

export default async function LabCreatorDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div>
      <CreatorDetail creatorId={id} />
    </div>
  );
}
