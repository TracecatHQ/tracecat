import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Secret stores | Organization",
}

export default function SecretStoresLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
