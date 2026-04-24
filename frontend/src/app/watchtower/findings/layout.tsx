import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Findings | Tracecat",
}

export default function FindingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
