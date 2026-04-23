import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Service accounts | Workspace",
}

export default function WorkspaceServiceAccountsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
