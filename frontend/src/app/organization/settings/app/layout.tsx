import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Application | Organization",
}

export default function AppSettingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
