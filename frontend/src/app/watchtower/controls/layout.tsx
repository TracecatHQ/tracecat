import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Controls | Tracecat",
}

export default function ControlsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
