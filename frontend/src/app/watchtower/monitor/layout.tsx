import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Monitor | Watchtower",
}

export default function MonitorLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
