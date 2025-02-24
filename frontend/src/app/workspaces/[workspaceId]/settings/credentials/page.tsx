"use client"

import { useWorkspace } from "@/providers/workspace"
import { PlusCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/workspaces/add-workspace-secret"
import { WorkspaceSecretsTable } from "@/components/workspaces/workspace-secrets-table"

export default function WorkspaceCredentialsPage() {
  const { workspace, workspaceError, workspaceLoading } = useWorkspace()
  if (workspaceLoading) {
    return <CenteredSpinner />
  }
  if (workspaceError) {
    return (
      <AlertNotification
        level="error"
        message="Error loading workspace info."
      />
    )
  }
  if (!workspace) {
    return <AlertNotification level="error" message="Workspace not found." />
  }
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Credentials</h2>
            <p className="text-md text-muted-foreground">
              Manage credentials for the <b className="inline-block">{workspace.name}</b> workspace.
            </p>
          </div>
        </div>

        <div className="space-y-4">
          <>
            <h6 className="text-sm font-semibold">Add secret</h6>
            <NewCredentialsDialog>
              <NewCredentialsDialogTrigger asChild>
                <Button
                  variant="outline"
                  role="combobox"
                  className="ml-auto space-x-2"
                >
                  <PlusCircle className="mr-2 size-4" />
                  Create new secret
                </Button>
              </NewCredentialsDialogTrigger>
            </NewCredentialsDialog>
          </>
          <>
            <h6 className="text-sm font-semibold">Manage secrets</h6>
            <WorkspaceSecretsTable />
          </>
        </div>
      </div>
    </div>
  )
}
