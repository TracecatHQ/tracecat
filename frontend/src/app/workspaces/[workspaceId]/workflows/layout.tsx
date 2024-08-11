import { type Metadata } from "next"

export const metadata: Metadata = {
  title: "Workflows",
}

export default async function WorkflowsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="no-scrollbar flex h-screen max-h-screen flex-col">
      {children}
    </div>
  )
}
