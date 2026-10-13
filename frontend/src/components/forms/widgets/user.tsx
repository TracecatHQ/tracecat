"use client"

import { CheckIcon, ChevronsUpDownIcon } from "lucide-react"
import { useState } from "react"
import type { FormUserRead } from "@/client"
import { formControlClass, formStyles } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
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
import UserAvatar from "@/components/user-avatar"
import { useDebounce } from "@/hooks/use-debounce"
import { useFormUserSearch } from "@/hooks/use-forms"
import { cn } from "@/lib/utils"

function getUserName(user: FormUserRead): string {
  return [user.first_name, user.last_name].filter(Boolean).join(" ")
}

function UserLabel({ user }: { user: FormUserRead }) {
  const name = getUserName(user)
  return (
    <span className="flex min-w-0 items-center gap-2">
      <UserAvatar
        email={user.email}
        firstName={user.first_name}
        className="size-5"
        fallbackClassName="text-[10px]"
      />
      {name && <span className="truncate">{name}</span>}
      <span className={cn("truncate", name && "text-muted-foreground")}>
        {user.email}
      </span>
    </span>
  )
}

function toSelection(value: unknown): FormUserRead[] {
  if (Array.isArray(value)) {
    return value as FormUserRead[]
  }
  return value ? [value as FormUserRead] : []
}

/**
 * Searchable picker of organization members.
 *
 * Form state holds the chosen user objects so their names stay on screen.
 * Only their IDs are submitted; the server expands them again.
 */
export function UserWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
  runtime,
}: FormWidgetProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [debouncedSearch] = useDebounce(search, 250)
  const { users, usersIsLoading, hasNextPage, fetchNextPage } =
    useFormUserSearch(
      { workspaceId: runtime.workspaceId, formId: runtime.formId },
      debouncedSearch,
      { enabled: open }
    )
  const selection = toSelection(value)
  const selectedIds = new Set(selection.map((user) => user.user_id))

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen)
    if (!nextOpen) {
      setSearch("")
      onBlur()
    }
  }

  function handleSelect(user: FormUserRead) {
    if (!field.multiple) {
      onChange(selectedIds.has(user.user_id) ? null : user)
      handleOpenChange(false)
      return
    }
    onChange(
      selectedIds.has(user.user_id)
        ? selection.filter((item) => item.user_id !== user.user_id)
        : [...selection, user]
    )
  }

  let summary = (
    <span className="text-muted-foreground">
      {field.placeholder ??
        (field.multiple ? "Select people" : "Select a person")}
    </span>
  )
  if (selection.length === 1) {
    summary = <UserLabel user={selection[0]} />
  } else if (selection.length > 1) {
    summary = <span>{selection.length} people selected</span>
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-invalid={invalid}
          aria-required={field.required}
          aria-describedby={describedBy(id, field, invalid)}
          disabled={disabled}
          className={formControlClass(
            invalid,
            "w-full justify-between gap-2 px-3 font-normal"
          )}
        >
          {summary}
          <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] p-0"
      >
        {/* The server filters, so cmdk must not filter again. */}
        <Command shouldFilter={false}>
          <CommandInput
            value={search}
            onValueChange={setSearch}
            placeholder="Search by name or email"
            className={formStyles.option}
          />
          <CommandList>
            <CommandEmpty className={cn(formStyles.option, "py-6 text-center")}>
              {usersIsLoading ? "Searching..." : "No people found."}
            </CommandEmpty>
            <CommandGroup>
              {users.map((user) => {
                const checked = selectedIds.has(user.user_id)
                return (
                  <CommandItem
                    key={user.user_id}
                    value={user.user_id}
                    onSelect={() => handleSelect(user)}
                    className={formStyles.option}
                  >
                    <CheckIcon
                      className={cn("size-4", !checked && "invisible")}
                    />
                    <UserLabel user={user} />
                    {checked && <span className="sr-only">, selected</span>}
                  </CommandItem>
                )
              })}
              {hasNextPage && (
                <CommandItem
                  value="__load-more__"
                  onSelect={() => fetchNextPage()}
                  className={cn(formStyles.option, "text-muted-foreground")}
                >
                  Load more
                </CommandItem>
              )}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
