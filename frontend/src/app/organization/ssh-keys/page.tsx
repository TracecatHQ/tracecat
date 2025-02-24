"use client"

import { PlusCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ConfirmationDialog } from "@/components/confirmation-dialog"
import { OrgSSHKeysTable } from "@/components/organization/org-secrets-table"
import {
  CreateOrgSSHKeyDialog,
  CreateOrgSSHKeyDialogTrigger,
} from "@/components/organization/org-ssh-key-create"

export default function SSHKeysPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">SSH keys</h2>
            <p className="text-md text-muted-foreground">
              View your organization-wide SSH keys here. Tracecat uses SSH keys
              to authenticate into your private action registry.
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <ConfirmationDialog
              title="Sync All Repositories"
              description="Are you sure you want to sync all repositories? This will replace all existing actions with the latest from the repositories."
              onConfirm={() => {}}
            ></ConfirmationDialog>
          </div>
        </div>
        <div className="space-y-4">
          <>
            <h6 className="text-sm font-semibold">Add secret</h6>
            <CreateOrgSSHKeyDialog>
              <CreateOrgSSHKeyDialogTrigger asChild>
                <Button
                  variant="outline"
                  role="combobox"
                  className="ml-auto space-x-2"
                >
                  <PlusCircle className="mr-2 size-4" />
                  Create new SSH key
                </Button>
              </CreateOrgSSHKeyDialogTrigger>
            </CreateOrgSSHKeyDialog>
          </>
          <>
            <h6 className="text-sm font-semibold">Manage secrets</h6>
            <OrgSSHKeysTable />
          </>
        </div>
      </div>
    </div>
  )
}
