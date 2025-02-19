"use client"

import React, { useState } from "react"
import { RegistryActionReadMinimal } from "@/client"

import { useRegistryActions } from "@/lib/hooks"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"

export function DeleteRegistryActionAlertDialog({
  selectedAction,
  setSelectedAction,
  children,
}: React.PropsWithChildren<{
  selectedAction: RegistryActionReadMinimal | null
  setSelectedAction: (selectedAction: RegistryActionReadMinimal | null) => void
}>) {
  const { deleteRegistryAction } = useRegistryActions()
  const [confirmationInput, setConfirmationInput] = useState<string>("")

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedAction(null)
        }
      }}
    >
      {children}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete action</AlertDialogTitle>
          <AlertDialogDescription className="flex flex-col space-y-2">
            <span>
              Are you sure you want to delete this action from the registry?
              This action cannot be undone.
            </span>
            <span>
              Please type the action name{" "}
              <p className="inline-block bg-muted font-mono tracking-tighter text-muted-foreground">
                {selectedAction?.action}
              </p>{" "}
              to confirm.
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Input
          value={confirmationInput}
          onChange={(e) => setConfirmationInput(e.target.value)}
          placeholder={selectedAction?.action}
          className="mb-4 font-mono tracking-tighter text-muted-foreground"
        />
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (!selectedAction) {
                return toast({
                  title: "No action selected",
                  description: "Please select an action to delete.",
                })
              } else if (confirmationInput !== selectedAction.action) {
                return toast({
                  title: "Action name doesn't match",
                  description: "Please try again.",
                })
              }
              console.log("Deleting action", selectedAction)
              try {
                await deleteRegistryAction({
                  actionName: selectedAction.action,
                })
              } finally {
                setSelectedAction(null)
                setConfirmationInput("")
              }
            }}
            disabled={
              !selectedAction || confirmationInput !== selectedAction.action
            }
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export const DeleteRegistryActionAlertDialogTrigger = AlertDialogTrigger
