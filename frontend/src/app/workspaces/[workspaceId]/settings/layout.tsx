import { Suspense } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { WorkspaceSettingsLayout } from "@/components/sidebar/workspace-settings-layout"

export default function WorkspaceSettingsLayoutPage({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <WorkspaceSettingsLayout>
      <div className="container my-16">
        <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
      </div>
    </WorkspaceSettingsLayout>
  )
}
