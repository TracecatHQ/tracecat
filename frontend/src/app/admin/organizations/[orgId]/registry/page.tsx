"use client"

import { ArrowLeftIcon } from "lucide-react"
import Link from "next/link"
import { use } from "react"
import { AdminOrgRegistryTable } from "@/components/admin/admin-org-registry-table"
import { useAdminOrganization } from "@/hooks/use-admin"

export default function AdminOrgRegistryPage({
  params,
}: {
  params: Promise<{ orgId: string }>
}) {
  const { orgId } = use(params)
  const { organization } = useAdminOrganization(orgId)

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/admin/organizations"
          className="flex items-center text-sm text-muted-foreground hover:text-foreground mb-4"
        >
          <ArrowLeftIcon className="mr-2 size-4" />
          Back to organizations
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">
          Registry repositories
        </h1>
        <p className="text-muted-foreground">
          Manage registry repositories for{" "}
          {organization?.name ?? "organization"}.
        </p>
      </div>
      <AdminOrgRegistryTable orgId={orgId} />
    </div>
  )
}
