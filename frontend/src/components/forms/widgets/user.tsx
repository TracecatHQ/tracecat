"use client"

import { CheckIcon, ChevronsUpDownIcon } from "lucide-react"
import { useState } from "react"
import type { FormUserRead } from "@/client"
import { formControlClass, formStyles } from "@/components/forms/form-styles"
import { controlProps, type FormWidgetProps } from "@/components/forms/widgets"
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

function UserLabel({ user }: { user: FormUserRead }) {
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ")
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

/**
 * Searchable picker of organization members.
 *
 * Form state holds the chosen user objects so their names stay on screen.
 * Only their IDs are submitted; the server expands them again.
 */
export function UserWidget(props: FormWidgetProps) {
  const { field, value, onChange, disabled, invalid } = props
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [debouncedSearch] = useDebounce(search, 250)
  const { users, usersIsLoading, error, hasNextPage, fetchNextPage } =
    useFormUserSearch(
      { workspaceId: props.workspaceId, formId: props.formId },
      debouncedSearch,
      { enabled: open }
    )
  // The query runs on the debounced text. Until it catches up, the loaded
  // list answers an older query, so it is not shown.
  const isCatchingUp = search.trim() !== debouncedSearch.trim()
  const visibleUsers = isCatchingUp ? [] : users
  const selection = (value ? [value].flat() : []) as FormUserRead[]
  const selectedIds = new Set(selection.map((user) => user.user_id))

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen)
    if (!nextOpen) setSearch("")
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

  let emptyMessage = "No people found."
  if (usersIsLoading || isCatchingUp) {
    emptyMessage = "Searching..."
  } else if (error) {
    emptyMessage = "Couldn't search people. Try again."
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
          {...controlProps(props)}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-required={field.required}
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
              {emptyMessage}
            </CommandEmpty>
            <CommandGroup>
              {visibleUsers.map((user) => {
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
              {hasNextPage && !isCatchingUp && (
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
