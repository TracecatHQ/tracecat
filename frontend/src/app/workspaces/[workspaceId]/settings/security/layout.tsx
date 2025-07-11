import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Security | Workspace",
}

export default function SecurityLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
