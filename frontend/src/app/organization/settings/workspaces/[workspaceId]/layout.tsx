import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Workspace settings | Organization",
}

export default function WorkspaceSettingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
