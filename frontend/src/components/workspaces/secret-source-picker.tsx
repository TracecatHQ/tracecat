"use client"

import { CheckIcon, ChevronsUpDownIcon } from "lucide-react"
import Image from "next/image"
import TracecatIcon from "public/icon.png"
import React from "react"
import type { SecretStoreProvider, WorkspaceSecretStoreRead } from "@/client"
import { SECRET_STORE_PROVIDERS } from "@/components/organization/secret-store-providers"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useAuthorizedSecretStores } from "@/hooks/use-secret-stores"
import { cn } from "@/lib/utils"

/** Search appears once the store list no longer fits at a glance. */
const SEARCH_THRESHOLD = 6

/** `null` means the secret is stored in Tracecat. */
export type SecretSourceValue = string | null

/**
 * Picks where a secret lives: Tracecat, or one specific authorized external
 * store. Stores are grouped under their provider, so one choice sets both.
 */
export function SecretSourcePicker({
  id,
  workspaceId,
  value,
  onChange,
}: {
  id?: string
  workspaceId: string
  value: SecretSourceValue
  onChange: (value: SecretSourceValue) => void
}) {
  const [open, setOpen] = React.useState(false)
  const { stores, isLoading, error } = useAuthorizedSecretStores(workspaceId)
  // Disabled and still-in-setup stores cannot resolve secrets.
  const enabledStores = (stores ?? []).filter((store) => store.enabled)
  const selected = enabledStores.find((store) => store.id === value) ?? null
  const isStale = stores !== undefined && value !== null && selected === null
  const selectedProvider = selected
    ? SECRET_STORE_PROVIDERS[selected.provider]
    : null
  const selectedHint = selected
    ? selectedProvider?.reference.storeHint(selected)
    : null
  const groups = groupByProvider(enabledStores)

  // A store disabled mid-dialog would leave the form writing to it.
  React.useEffect(() => {
    if (isStale) onChange(null)
  }, [isStale, onChange])

  function choose(next: SecretSourceValue) {
    onChange(next)
    setOpen(false)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-start gap-2 px-3 text-sm font-normal shadow-none"
        >
          {selectedProvider ? (
            <selectedProvider.Icon className="size-4 shrink-0 rounded-sm" />
          ) : (
            <Image src={TracecatIcon} alt="" className="size-4 rounded-sm" />
          )}
          <span className="min-w-0 flex-1 truncate text-left">
            {selected ? selected.name : "Tracecat"}
            {selectedHint && (
              <span className="text-muted-foreground"> · {selectedHint}</span>
            )}
          </span>
          <ChevronsUpDownIcon className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[--radix-popover-trigger-width] p-0"
      >
        <Command>
          {enabledStores.length > SEARCH_THRESHOLD && (
            <CommandInput placeholder="Search stores" />
          )}
          <CommandList className="max-h-72">
            <CommandEmpty>No stores match.</CommandEmpty>
            <CommandItem
              value="tracecat"
              onSelect={() => choose(null)}
              className="gap-2"
            >
              <Image src={TracecatIcon} alt="" className="size-4 rounded-sm" />
              <span className="flex-1">Tracecat</span>
              <CheckIcon
                className={cn("size-4", value !== null && "invisible")}
              />
            </CommandItem>
            {groups.map(([provider, providerStores]) => {
              const entry = SECRET_STORE_PROVIDERS[provider]
              return (
                <CommandGroup
                  key={provider}
                  heading={
                    <span className="flex items-center gap-1.5">
                      <entry.Icon className="size-3.5 rounded-sm" />
                      {entry.label}
                    </span>
                  }
                >
                  {providerStores.map((store) => {
                    const hint = entry.reference.storeHint(store)
                    return (
                      <CommandItem
                        key={store.id}
                        value={`${store.name} ${hint ?? ""}`}
                        onSelect={() => choose(store.id)}
                        className="gap-2 pl-7"
                      >
                        <span className="min-w-0 flex-1 truncate">
                          {store.name}
                        </span>
                        {hint && (
                          <span className="font-mono text-xs text-muted-foreground">
                            {hint}
                          </span>
                        )}
                        <CheckIcon
                          className={cn(
                            "size-4",
                            value !== store.id && "invisible"
                          )}
                        />
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              )
            })}
            {isLoading && (
              <p className="py-1.5 pl-7 pr-2 text-xs text-muted-foreground">
                Loading stores…
              </p>
            )}
            {error && (
              <p className="py-1.5 pl-7 pr-2 text-xs text-destructive">
                Could not load secret stores. Reopen this dialog to try again.
              </p>
            )}
            {!isLoading && !error && enabledStores.length === 0 && (
              <p className="py-1.5 pl-7 pr-2 text-xs text-muted-foreground">
                {stores?.length
                  ? "No stores are enabled for this workspace. Ask an organization admin to enable one or finish its setup."
                  : "No stores are authorized for this workspace. Ask an organization admin to add one."}
              </p>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/** Stores grouped under their provider, in the registry's provider order. */
function groupByProvider(
  stores: WorkspaceSecretStoreRead[]
): [SecretStoreProvider, WorkspaceSecretStoreRead[]][] {
  return (Object.keys(SECRET_STORE_PROVIDERS) as SecretStoreProvider[])
    .map(
      (provider) =>
        [provider, stores.filter((store) => store.provider === provider)] as [
          SecretStoreProvider,
          WorkspaceSecretStoreRead[],
        ]
    )
    .filter(([, providerStores]) => providerStores.length > 0)
}
