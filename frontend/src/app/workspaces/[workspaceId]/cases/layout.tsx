import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Cases",
}

export default function CasesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
