import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Case details",
}

export default function CaseDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="h-full bg-background rounded-lg shadow-sm">{children}</div>
  )
}
