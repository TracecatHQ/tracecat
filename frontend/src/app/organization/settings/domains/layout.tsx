import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Domains | Organization",
}

export default function DomainsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
