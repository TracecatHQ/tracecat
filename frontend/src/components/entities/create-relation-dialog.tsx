"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { AlertCircle as AlertCircleIcon, Link, Network } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { RelationType } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
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
import { useEntities } from "@/lib/hooks/use-entities"
import { useWorkspace } from "@/providers/workspace"

const relationTypes: {
  value: RelationType
  label: string
  icon: React.ElementType
}[] = [
  { value: "one_to_one", label: "One-to-one", icon: Link },
  { value: "one_to_many", label: "One-to-many", icon: Network },
  { value: "many_to_one", label: "Many-to-one", icon: Link },
  { value: "many_to_many", label: "Many-to-many", icon: Network },
]

const createRelationSchema = z.object({
  source_key: z
    .string()
    .min(1, "Key is required")
    .regex(
      /^[a-z][a-z0-9_]*$/,
      "Must start with a letter, be lowercase, and use letters, numbers, underscores"
    ),
  display_name: z.string().min(1, "Display name is required"),
  relation_type: z.enum([
    "one_to_one",
    "one_to_many",
    "many_to_one",
    "many_to_many",
  ] as const),
  target_entity_id: z.string().uuid("Select a target entity"),
  source_entity_id: z.string().uuid("Select a source entity"),
})

export type CreateRelationFormData = z.infer<typeof createRelationSchema>

interface CreateRelationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: CreateRelationFormData) => Promise<void>
  errorMessage?: string
  sourceEntityId?: string
}

export function CreateRelationDialog({
  open,
  onOpenChange,
  onSubmit,
  errorMessage,
  sourceEntityId,
}: CreateRelationDialogProps) {
  const { workspaceId } = useWorkspace()
  const { entities } = useEntities(workspaceId || "", false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const activeEntities = useMemo(
    () => (entities || []).filter((e) => e.is_active),
    [entities]
  )

  const form = useForm<CreateRelationFormData>({
    resolver: zodResolver(createRelationSchema),
    defaultValues: {
      source_key: "",
      display_name: "",
      relation_type: "one_to_one",
      target_entity_id: "",
      source_entity_id: sourceEntityId || undefined,
    },
  })

  // Keep form source_entity_id in sync when preselected changes
  useEffect(() => {
    if (sourceEntityId) {
      form.setValue("source_entity_id", sourceEntityId)
    }
  }, [sourceEntityId])

  const handleSubmit = async (data: CreateRelationFormData) => {
    setIsSubmitting(true)
    try {
      setSubmitError(null)
      await onSubmit(data)
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to create relation:", error)
      let message = "Failed to create the relation. Please try again."
      if (error && typeof error === "object") {
        const err = error as {
          body?: {
            detail?: string | string[]
            message?: string
            error?: string
          }
          message?: string
          status?: number
          statusText?: string
        }
        const detail = err.body?.detail
        if (Array.isArray(detail)) {
          message = detail.join("\n")
        } else {
          message =
            (typeof detail === "string" && detail) ||
            err.body?.message ||
            err.body?.error ||
            (err.status && err.statusText
              ? `${err.status} ${err.statusText}`
              : err.message) ||
            message
        }
      }
      setSubmitError(message)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Add relation</DialogTitle>
          <DialogDescription>
            Create a relation between two entities in your workspace.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-4"
          >
            {(submitError || errorMessage) && (
              <Alert variant="destructive">
                <AlertCircleIcon />
                <AlertTitle>Failed to create relation</AlertTitle>
                <AlertDescription>
                  {submitError || errorMessage}
                </AlertDescription>
              </Alert>
            )}

            <FormField
              control={form.control}
              name="source_key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Identifier / Slug</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Lowercase, no spaces"
                      {...field}
                      onChange={(e) =>
                        field.onChange(e.target.value.toLowerCase())
                      }
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="display_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder="Human-readable name" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="relation_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Relation type</FormLabel>
                  <Select onValueChange={field.onChange} value={field.value}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select relation type" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {relationTypes.map((t) => {
                        const Icon = t.icon
                        return (
                          <SelectItem key={t.value} value={t.value}>
                            <div className="flex items-center gap-2">
                              <Icon className="h-4 w-4" />
                              <span>{t.label}</span>
                            </div>
                          </SelectItem>
                        )
                      })}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="source_entity_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Source entity</FormLabel>
                  <Select
                    onValueChange={field.onChange}
                    value={field.value || ""}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select source entity" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {activeEntities.map((e) => (
                        <SelectItem key={e.id} value={e.id}>
                          {e.display_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="target_entity_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Target entity</FormLabel>
                  <Select onValueChange={field.onChange} value={field.value}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select target entity" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {activeEntities.map((e) => (
                        <SelectItem key={e.id} value={e.id}>
                          {e.display_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Creating..." : "Create relation"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
