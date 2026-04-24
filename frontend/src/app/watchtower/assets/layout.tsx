import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Assets | Tracecat",
}

export default function AssetsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
