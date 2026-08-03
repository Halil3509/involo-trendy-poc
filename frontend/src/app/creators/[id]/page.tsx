import { redirect } from "next/navigation";

export default async function CreatorDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/lab/creators/${id}`);
}
