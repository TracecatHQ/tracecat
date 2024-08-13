"use client"

import { redirect } from "next/navigation"

import { useWorkspace } from "@/lib/hooks"

export default function SettingsPage() {
  const { workspaceId } = useWorkspace()
  return redirect(`/workspaces/${workspaceId}/settings/general`)
}
