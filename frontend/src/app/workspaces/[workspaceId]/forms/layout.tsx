import type { Metadata } from "next"
import { FormsRouteLayout } from "@/components/forms/forms-route-layout"

export const metadata: Metadata = {
  title: "Forms",
}

export default function FormsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return <FormsRouteLayout>{children}</FormsRouteLayout>
}
