import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Agents",
}

export default function AgentsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
