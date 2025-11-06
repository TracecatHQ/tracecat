"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  ChevronDownIcon,
  CopyIcon,
  DatabaseZapIcon,
  PencilIcon,
  Trash2Icon,
} from "lucide-react"
import { useParams } from "next/navigation"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  type TableColumnRead,
  type TablesUpdateColumnData,
} from "@/client"
import { SqlTypeDisplay } from "@/components/data-type/sql-type-display"
import { Spinner } from "@/components/loading/spinner"
import { CustomTagInput } from "@/components/tags-input"
import { getColumnEnumValues } from "@/lib/tables"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import type { TracecatApiError } from "@/lib/errors"
import { useDeleteColumn, useUpdateColumn } from "@/lib/hooks"
import {
  parseEnumValuesInput,
  SqlTypeEnum,
} from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

type TableViewColumnMenuType = "delete" | "edit" | "set-natural-key" | null

export function TableViewColumnMenu({ column }: { column: TableColumnRead }) {
  const { user } = useAuth()
  const params = useParams<{ tableId?: string }>()
  const tableId = params?.tableId
  const [activeType, setActiveType] = useState<TableViewColumnMenuType>(null)
  const handleDialogClose = () => setActiveType(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="size-4 p-0 !ring-0">
            <span className="sr-only">Configure column</span>
            <ChevronDownIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(column.id))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy ID
          </DropdownMenuItem>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(column.name))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy name
          </DropdownMenuItem>
          {user?.isPrivileged() && (
            <>
              <DropdownMenuItem
                className="py-1 text-xs text-foreground/80"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("edit")
                }}
              >
                <PencilIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                Edit column
              </DropdownMenuItem>
              <DropdownMenuItem
                className="py-1 text-xs text-foreground/80"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("set-natural-key")
                }}
                disabled={column.is_index}
              >
                <DatabaseZapIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                {column.is_index ? "Unique index" : "Create unique index"}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="py-1 text-xs text-destructive"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("delete")
                }}
              >
                <Trash2Icon className="mr-2 size-3 group-hover/item:text-destructive" />
                Delete column
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {activeType === "delete" ? (
        <TableColumnDeleteDialog
          tableId={tableId}
          column={column}
          open
          onOpenChange={handleDialogClose}
        />
      ) : null}
      {activeType === "set-natural-key" ? (
        <TableColumnIndexDialog
          tableId={tableId}
          column={column}
          open
          onOpenChange={handleDialogClose}
        />
      ) : null}
      {activeType === "edit" ? (
        <TableColumnEditDialog
          tableId={tableId}
          column={column}
          open
          onOpenChange={handleDialogClose}
        />
      ) : null}
    </>
  )
}

function TableColumnDeleteDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { deleteColumn } = useDeleteColumn()
  const [confirmName, setConfirmName] = useState("")

  if (!tableId || !workspaceId) {
    return null
  }

  const handleDeleteColumn = async () => {
    if (confirmName !== column.name) {
      toast({
        title: "Column name does not match",
        description: "Please type the exact column name to confirm deletion",
      })
      return
    }

    try {
      await deleteColumn({
        tableId,
        workspaceId,
        columnId: column.id,
      })
      onOpenChange()
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete column permanently</AlertDialogTitle>
          <AlertDialogDescription>
            To confirm deletion, type the column name <b>{column.name}</b>{" "}
            below. This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="my-4">
          <Input
            placeholder={`Type "${column.name}" to confirm`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDeleteColumn}
            variant="destructive"
            disabled={confirmName !== column.name}
          >
            Delete column
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

type SqlTypeSelectable = (typeof SqlTypeEnum)[number]

const updateColumnFormSchema = z
  .object({
    name: z
      .string()
      .min(1, { message: "Name must be at least 1 character" })
      .max(255, { message: "Name must be less than 255 characters" })
         .regex(/^[a-zA-Z0-9_]+$/, {
      message: "Name must contain only letters, numbers, and underscores",
         }),
    type: z.enum(SqlTypeEnum),
    nullable: z.boolean(),
    defaultValue: z.string().optional(),
    enumValues: z.string().optional(),
    enumDefault: z.string().optional(),
    isIndex: z.boolean().default(false),
  })
  .superRefine((data, ctx) => {
    if (data.type === "ENUM") {
      const parsedValues = parseEnumValuesInput(data.enumValues)
      if (parsedValues.length === 0) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Provide at least one enum value",
          path: ["enumValues"],
        })
      }
      const trimmedDefault = (data.enumDefault ?? "").trim()
      if (trimmedDefault && !parsedValues.includes(trimmedDefault)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Default must match one of the enum values",
          path: ["enumDefault"],
        })
      }
    }
  })

type UpdateColumnFormData = z.infer<typeof updateColumnFormSchema>

const NO_DEFAULT_VALUE = "__tracecat_no_default__"

function TableColumnEditDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { updateColumn, updateColumnIsPending } = useUpdateColumn()

  const initialEnumMetadata = useMemo(
    () => extractEnumMetadata(column),
    [column]
  )
  const initialDefaults = useMemo(() => {
    const isEnum = column.type === "ENUM"
    return {
      name: column.name,
      type: column.type as SqlTypeSelectable,
      nullable: column.nullable ?? true,
      defaultValue: isEnum ? "" : normaliseDefaultForDisplay(column.default),
      enumValues: isEnum ? initialEnumMetadata.values.join("\n") : "",
      enumDefault:
        isEnum && initialEnumMetadata.defaultValue
          ? initialEnumMetadata.defaultValue
          : "",
      isIndex: column.is_index ?? false,
    }
  }, [column, initialEnumMetadata])

  const form = useForm<UpdateColumnFormData>({
    resolver: zodResolver(updateColumnFormSchema),
    defaultValues: initialDefaults,
  })

  const typeValue = form.watch("type")
  const enumValuesRaw = form.watch("enumValues")
  const parsedEnumValues = useMemo(
    () => parseEnumValuesInput(enumValuesRaw),
    [enumValuesRaw]
  )

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      onOpenChange()
    }
  }

  const onSubmit = async (data: UpdateColumnFormData) => {
    if (!tableId || !workspaceId) {
      return
    }

    const typeChanged = data.type !== column.type
    const enumValuesChanged =
      data.type === "ENUM" &&
      parseEnumValuesInput(data.enumValues).join("|") !==
        initialEnumMetadata.values.join("|")

    if (
      (typeChanged || enumValuesChanged) &&
      !window.confirm(
        "Changing the column type or enum options will reset existing values to the column default (or null). Continue?"
      )
    ) {
      return
    }

    const requestBody: TablesUpdateColumnData["requestBody"] = {}

    if (data.name !== column.name) {
      requestBody.name = data.name
    }
    if (typeChanged) {
      requestBody.type = data.type
    }
    if (data.nullable !== column.nullable) {
      requestBody.nullable = data.nullable
    }
    if (data.isIndex !== (column.is_index ?? false)) {
      requestBody.is_index = data.isIndex
    }

    if (data.type === "ENUM") {
      const values = parseEnumValuesInput(data.enumValues)
      const defaultOption = (data.enumDefault ?? "").trim() || undefined

      const nextMetadata: Record<string, unknown> = {
        enum_values: values,
      }
      if (defaultOption) {
        nextMetadata.default = defaultOption
        nextMetadata.value = defaultOption
      }

      const metadataChanged =
        column.type !== "ENUM" ||
        !arraysEqual(initialEnumMetadata.values, values) ||
        (initialEnumMetadata.defaultValue ?? "") !== (defaultOption ?? "")

      if (metadataChanged) {
        requestBody.default = nextMetadata
      }
    } else {
      const trimmedDefault = (data.defaultValue ?? "").trim()
      const originalDefault = (initialDefaults.defaultValue ?? "").trim()

      if (typeChanged) {
        if (trimmedDefault.length === 0) {
          if (column.default != null) {
            requestBody.default = null
          }
        } else {
          try {
            requestBody.default = parseDefaultInput(data.type, trimmedDefault)
          } catch (error) {
            form.setError("defaultValue", {
              message:
                error instanceof Error
                  ? error.message
                  : "Invalid default value",
            })
            return
          }
        }
      } else if (
        trimmedDefault.length === 0 &&
        originalDefault.length > 0 &&
        column.default != null
      ) {
        requestBody.default = null
      } else if (
        trimmedDefault.length > 0 &&
        trimmedDefault !== originalDefault
      ) {
        try {
          requestBody.default = parseDefaultInput(data.type, trimmedDefault)
        } catch (error) {
          form.setError("defaultValue", {
            message:
              error instanceof Error ? error.message : "Invalid default value",
          })
          return
        }
      }
    }

    if (Object.keys(requestBody).length === 0) {
      onOpenChange()
      return
    }

    try {
      await updateColumn({
        tableId,
        columnId: column.id,
        workspaceId,
        requestBody,
      })
      toast({
        title: "Column updated",
        description: `Column ${data.name} was successfully updated.`,
      })
      onOpenChange()
    } catch (error) {
      console.error("Error updating column:", error)
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError
        if (apiError.status === 409) {
          // Handle unique constraint violation
          const errorMessage =
            typeof apiError.body.detail === "string"
              ? apiError.body.detail
              : "A column with this name already exists"
          form.setError("name", {
            type: "manual",
            message: errorMessage,
          })
        } else if (apiError.status === 422) {
          // Handle validation errors
          const detail = apiError.body.detail
          if (Array.isArray(detail)) {
            // Pydantic validation errors
            detail.forEach((err: { loc?: string[]; msg?: string }) => {
              if (err.loc && err.loc.length > 0) {
                const fieldName = err.loc[
                  err.loc.length - 1
                ] as keyof UpdateColumnFormData
                form.setError(fieldName, {
                  type: "manual",
                  message: err.msg || "Validation error",
                })
              }
            })
          } else if (typeof detail === "string") {
            form.setError("root", {
              type: "manual",
              message: detail,
            })
          }
        } else {
          form.setError("root", {
            type: "manual",
            message:
              typeof apiError.body.detail === "string"
                ? apiError.body.detail
                : apiError.message || "Failed to update column",
          })
        }
      } else {
        form.setError("root", {
          type: "manual",
          message:
            error instanceof Error ? error.message : "Failed to update column",
        })
      }
    }
  }

  useEffect(() => {
    if (open) {
      form.reset(initialDefaults)
    }
  }, [open, initialDefaults, form])

  if (!tableId || !workspaceId) {
    return null
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent className="sm:max-w-[520px]">
        <AlertDialogHeader>
          <AlertDialogTitle>Edit column</AlertDialogTitle>
          <AlertDialogDescription>
            Update the column definition. If you change the type or enum
            options, existing values will be reset to the column default (or
            cleared when no default is set).
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Column name</FormLabel>
                  <FormControl>
                    <Input {...field} autoComplete="off" />
                  </FormControl>
                  <FormDescription>
                    Must start with a letter or underscore; other characters are
                    removed.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Type</FormLabel>
                  <FormControl>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a type..." />
                      </SelectTrigger>
                      <SelectContent>
                        {SqlTypeEnum.map((type) => (
                          <SelectItem key={type} value={type}>
                            <SqlTypeDisplay
                              type={type}
                              labelClassName="text-xs"
                            />
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="nullable"
                render={({ field }) => (
                  <FormItem className="flex flex-col space-y-1 rounded-md border border-muted p-3">
                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <FormLabel>Allow null</FormLabel>
                        <FormDescription>
                          Enable to allow rows without a value.
                        </FormDescription>
                      </div>
                      <FormControl>
                        <Switch
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="isIndex"
                render={({ field }) => (
                  <FormItem className="flex flex-col space-y-1 rounded-md border border-muted p-3">
                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <FormLabel>Unique values</FormLabel>
                        <FormDescription>
                          Require this column to contain unique values.
                        </FormDescription>
                      </div>
                      <FormControl>
                        <Switch
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            {typeValue === "ENUM" ? (
              <>
                <FormField
                  control={form.control}
                  name="enumValues"
                  render={({ field }) => {
                    const enumValues = parseEnumValuesInput(field.value)
                    return (
                      <FormItem>
                        <FormLabel>Enum values</FormLabel>
                        <FormDescription>
                          Press Enter to add each value.
                        </FormDescription>
                        <FormControl>
                          <CustomTagInput
                            tags={enumValues.map((val, i) => ({
                              id: `${i}`,
                              text: val,
                            }))}
                            setTags={(newTags) => {
                              const resolvedTags =
                                typeof newTags === "function"
                                  ? newTags([])
                                  : newTags
                              field.onChange(
                                resolvedTags.map((tag) => tag.text).join("\n")
                              )
                            }}
                            placeholder="Add enum values..."
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )
                  }}
                />
                <FormField
                  control={form.control}
                  name="enumDefault"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Default value</FormLabel>
                      <FormDescription>
                        Optional. Must match one of the enum values.
                      </FormDescription>
                      <FormControl>
                        <Select
                          value={
                            field.value && field.value.length > 0
                              ? field.value
                              : NO_DEFAULT_VALUE
                          }
                          onValueChange={(next) =>
                            field.onChange(
                              next === NO_DEFAULT_VALUE ? "" : next
                            )
                          }
                          disabled={parsedEnumValues.length === 0}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="No default" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={NO_DEFAULT_VALUE}>
                              No default
                            </SelectItem>
                            {parsedEnumValues.map((value) => (
                              <SelectItem key={value} value={value}>
                                {value}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </>
            ) : (
              <FormField
                control={form.control}
                name="defaultValue"
                render={({ field }) => {
                  if (typeValue === "BOOLEAN") {
                    return (
                      <FormItem>
                        <FormLabel>Default value</FormLabel>
                        <FormDescription>
                          Select a default, or leave empty for no default.
                        </FormDescription>
                        <FormControl>
                          <Select
                            value={
                              field.value && field.value.length > 0
                                ? field.value
                                : NO_DEFAULT_VALUE
                            }
                            onValueChange={(next) =>
                              field.onChange(
                                next === NO_DEFAULT_VALUE ? "" : next
                              )
                            }
                          >
                            <SelectTrigger>
                              <SelectValue placeholder="No default" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value={NO_DEFAULT_VALUE}>
                                No default
                              </SelectItem>
                              <SelectItem value="true">true</SelectItem>
                              <SelectItem value="false">false</SelectItem>
                            </SelectContent>
                          </Select>
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )
                  }

                  if (typeValue === "JSONB") {
                    return (
                      <FormItem>
                        <FormLabel>Default value</FormLabel>
                        <FormDescription>
                          Provide a JSON object/array, or leave empty.
                        </FormDescription>
                        <FormControl>
                          <Textarea rows={4} {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )
                  }

                  return (
                    <FormItem>
                      <FormLabel>Default value</FormLabel>
                      <FormDescription>
                        Leave empty to drop the existing default.
                      </FormDescription>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )
                }}
              />
            )}
            <AlertDialogFooter>
              <AlertDialogCancel disabled={updateColumnIsPending}>
                Cancel
              </AlertDialogCancel>
              <Button type="submit" disabled={updateColumnIsPending}>
                {updateColumnIsPending && (
                  <Spinner className="mr-2 size-3 text-background" />
                )}
                Save changes
              </Button>
            </AlertDialogFooter>
          </form>
        </Form>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function TableColumnIndexDialog({
  tableId,
  column,
  open,
  onOpenChange,
}: {
  tableId?: string
  column: TableColumnRead
  open: boolean
  onOpenChange: () => void
}) {
  const workspaceId = useWorkspaceId()
  const { updateColumn, updateColumnIsPending } = useUpdateColumn()

  if (!tableId || !workspaceId) {
    return null
  }

  if (column.is_index) {
    return (
      <AlertDialog open={open} onOpenChange={onOpenChange}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Column is already a unique index
            </AlertDialogTitle>
            <AlertDialogDescription>
              Column <b>{column.name}</b> is already a unique index.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Close</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    )
  }

  const handleSetIndex = async () => {
    try {
      const updates = {
        is_index: true,
      }

      await updateColumn({
        tableId,
        columnId: column.id,
        workspaceId,
        requestBody: updates,
      })

      toast({
        title: "Created unique index",
        description: "Column is now a unique index.",
      })

      onOpenChange()
    } catch (error) {
      console.error("Error creating unique index:", error)
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={() => {
        if (!updateColumnIsPending) {
          onOpenChange()
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Create unique index</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to make column <b>{column.name}</b> a unique
            index? This enables upsert operations on the table.
            <br />
            <br />
            <strong>Requirements:</strong>
            <ul className="mt-2 list-disc pl-5 text-xs">
              <li>All values in the column must be unique</li>
              <li>This cannot be undone except by recreating the column</li>
            </ul>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={updateColumnIsPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleSetIndex}
            disabled={updateColumnIsPending}
          >
            {updateColumnIsPending ? (
              <>
                <Spinner />
                Creating...
              </>
            ) : (
              <>
                <DatabaseZapIcon className="mr-2 size-4" />
                Create unique index
              </>
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function extractEnumMetadata(column: TableColumnRead): {
  values: string[]
  defaultValue?: string
} {
  const raw = column.default
  if (!raw || typeof raw !== "object") {
    const values = getColumnEnumValues(column)
    return { values, defaultValue: undefined }
  }

  const metadata = raw as Record<string, unknown>
  const rawValues = Array.isArray(metadata.enum_values)
    ? metadata.enum_values
    : Array.isArray(metadata.values)
      ? metadata.values
      : []

  const values = rawValues
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.trim())
    .filter(
      (value, index, self) => value.length > 0 && self.indexOf(value) === index
    )

  const defaultValue = [metadata.default, metadata.value].find(
    (candidate): candidate is string => typeof candidate === "string"
  )

  return {
    values,
    defaultValue,
  }
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) {
    return false
  }
  return a.every((value, index) => value === b[index])
}

function normaliseDefaultForDisplay(raw: unknown): string {
  if (raw == null) {
    return ""
  }
  if (typeof raw !== "string") {
    return String(raw)
  }

  const castMatch = raw.match(/^'(.*)'::[a-zA-Z0-9_]+$/)
  if (castMatch) {
    return castMatch[1]
  }

  if (raw.startsWith("'") && raw.endsWith("'")) {
    return raw.slice(1, -1)
  }

  return raw
}

function parseDefaultInput(type: SqlTypeSelectable, raw: string): unknown {
  switch (type) {
    case "BOOLEAN": {
      const lower = raw.toLowerCase()
      if (lower === "true" || lower === "1") return true
      if (lower === "false" || lower === "0") return false
      throw new Error("Boolean defaults must be true/false or 1/0")
    }
    case "INTEGER": {
      const next = Number.parseInt(raw, 10)
      if (Number.isNaN(next)) {
        throw new Error("Integer defaults must be numeric")
      }
      return next
    }
    case "NUMERIC": {
      const next = Number(raw)
      if (Number.isNaN(next)) {
        throw new Error("Numeric defaults must be a valid number")
      }
      return next
    }
    case "JSONB": {
      try {
        const parsed = JSON.parse(raw)
        return JSON.stringify(parsed)
      } catch (error) {
        throw new Error(
          error instanceof Error ? error.message : "Invalid JSON default"
        )
      }
    }
    default:
      return raw
  }
}
