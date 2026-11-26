"use client"

import { notFound, useParams } from "next/navigation"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { TriggerDetail } from "@/components/triggers/trigger-detail"

export default function TriggerDetailPage() {
  const params = useParams<{ triggerId: string }>()
  const canReadTriggers = useScopeCheck("trigger:read")

  if (canReadTriggers === false) {
    notFound()
  }
  if (canReadTriggers === undefined || !params?.triggerId) {
    return null
  }

  return <TriggerDetail triggerId={params.triggerId} />
}
