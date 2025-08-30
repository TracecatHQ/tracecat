import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Records",
}

export default function RecordsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
