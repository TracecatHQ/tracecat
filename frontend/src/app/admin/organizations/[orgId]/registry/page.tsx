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
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div>
          <Link
            href="/admin/organizations"
            className="flex items-center text-sm text-muted-foreground hover:text-foreground mb-4"
          >
            <ArrowLeftIcon className="mr-2 size-4" />
            Back to organizations
          </Link>
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">
                Registry repositories
              </h2>
              <p className="text-base text-muted-foreground">
                Manage registry repositories for{" "}
                {organization?.name ?? "organization"}.
              </p>
            </div>
          </div>
        </div>
        <AdminOrgRegistryTable orgId={orgId} />
      </div>
    </div>
  )
}
