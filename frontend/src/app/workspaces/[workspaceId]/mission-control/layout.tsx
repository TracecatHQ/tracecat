import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Mission Control",
}

export default function MissionControlLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
