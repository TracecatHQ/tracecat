import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Agent | Organization",
}

export default function AgentLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
