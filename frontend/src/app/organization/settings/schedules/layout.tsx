import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Schedules | Organization",
}

export default function SchedulesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
