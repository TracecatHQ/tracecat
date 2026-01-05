"use client"

import { Search } from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import { SecretIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Item, ItemContent, ItemMedia, ItemTitle } from "@/components/ui/item"
import { useSecretDefinitions } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function SecretsCatalogPage() {
  const workspaceId = useWorkspaceId()
  const [searchQuery, setSearchQuery] = useState("")

  const {
    secretDefinitions,
    secretDefinitionsIsLoading,
    secretDefinitionsError,
  } = useSecretDefinitions(workspaceId)

  const filteredSecrets = useMemo(() => {
    if (!secretDefinitions) return []

    return secretDefinitions
      .filter((secret) =>
        secret.name.toLowerCase().includes(searchQuery.toLowerCase())
      )
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [secretDefinitions, searchQuery])

  if (secretDefinitionsIsLoading) {
    return <CenteredSpinner />
  }

  if (secretDefinitionsError) {
    return <div>Error: {secretDefinitionsError.message}</div>
  }

  return (
    <div className="mx-auto my-16 flex max-w-5xl flex-col px-8">
      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            placeholder="Search secrets..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-9 border-gray-300 bg-gray-50 pl-10 text-sm focus:border-gray-400 focus:bg-white"
          />
        </div>
      </div>

      {/* Secrets List */}
      <div className="grid grid-cols-2 gap-2">
        {filteredSecrets.map((secret) => (
          <Item
            key={secret.name}
            variant="outline"
            className="cursor-pointer hover:bg-muted/50"
            asChild
          >
            <Link
              href={`/workspaces/${workspaceId}/credentials/catalog/${secret.name}`}
            >
              <ItemMedia>
                <SecretIcon
                  secretName={secret.name}
                  className="size-7 rounded"
                />
              </ItemMedia>
              <ItemContent>
                <ItemTitle className="text-sm">{secret.name}</ItemTitle>
              </ItemContent>
              <Badge variant="secondary" className="text-xs">
                {secret.action_count}
              </Badge>
            </Link>
          </Item>
        ))}
      </div>

      {filteredSecrets.length === 0 && (
        <div className="col-span-2 py-12 text-center">
          <p className="text-sm text-muted-foreground">
            No secrets found matching your search.
          </p>
        </div>
      )}
    </div>
  )
}
