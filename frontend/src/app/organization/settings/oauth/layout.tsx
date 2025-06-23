import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "OAuth | Organization",
}

export default function OAuthLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
