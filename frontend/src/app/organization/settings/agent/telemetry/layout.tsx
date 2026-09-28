import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Telemetry | Organization",
}

export default function AgentTelemetryLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
