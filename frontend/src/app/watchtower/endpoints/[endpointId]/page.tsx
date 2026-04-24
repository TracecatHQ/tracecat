"use client"

import { SpmEndpointDetailView } from "@/components/spm/spm-ui"

export default function SpmEndpointDetailPage({
  params,
}: {
  params: { endpointId: string }
}) {
  return <SpmEndpointDetailView endpointId={params.endpointId} />
}
