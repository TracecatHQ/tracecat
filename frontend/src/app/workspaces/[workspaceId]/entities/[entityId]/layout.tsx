import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Entity details | Workspace",
}

export default function EntityDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
