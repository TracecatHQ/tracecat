"use client"

import { RefreshCcw } from "lucide-react"
import { RegistryRepositoriesTable } from "@/components/registry/registry-repos-table"
import { Button } from "@/components/ui/button"
import { useRegistryRepositoriesReload } from "@/lib/hooks"
import { useAuth } from "@/providers/auth"

export default function RegistryRepositoriesPage() {
  const { user } = useAuth()
  const { reloadRegistryRepositories, reloadRegistryRepositoriesIsPending } =
    useRegistryRepositoriesReload()
  const refreshRepositories = async () => {
    try {
      await reloadRegistryRepositories()
    } catch (error) {
      console.log("Error reloading repositories", error)
    }
  }
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Repositories
            </h2>
            <p className="text-md text-muted-foreground">
              View your organization&apos;s action repositories here.
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            {user?.isOrgAdmin() && (
              <Button
                role="combobox"
                variant="outline"
                className="items-center space-x-2"
                disabled={reloadRegistryRepositoriesIsPending}
                onClick={refreshRepositories}
              >
                <RefreshCcw className="size-4 text-muted-foreground/80" />
                <span>Refresh repositories</span>
              </Button>
            )}
          </div>
        </div>
        <RegistryRepositoriesTable />
      </div>
    </div>
  )
}
