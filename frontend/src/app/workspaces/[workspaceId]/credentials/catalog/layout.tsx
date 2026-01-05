import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Secrets catalog | Workspace",
}

export default function SecretsCatalogLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
