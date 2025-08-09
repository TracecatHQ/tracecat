import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Entity Details | Workspace",
}

export default function EntityDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
