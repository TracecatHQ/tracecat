import { Metadata } from "next"

export const metadata: Metadata = {
  title: "Tables",
}

export default function TablesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return <div>{children}</div>
}
