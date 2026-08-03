import { LabShell } from "@/components/lab-shell";

export default function LabLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <LabShell>{children}</LabShell>;
}
