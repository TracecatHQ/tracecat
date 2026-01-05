"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ChevronLeft, Loader2 } from "lucide-react"
import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type { SecretCreate } from "@/client"
import { SecretIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useSecretDefinitions, useWorkspaceSecrets } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const createSecretSchema = z
  .object({
    name: z.string().min(1, "Name is required"),
    description: z.string().max(255).optional(),
    environment: z.string().default("default"),
    keys: z.array(
      z.object({
        key: z.string(),
        value: z.string(),
        isOptional: z.boolean().default(false),
      })
    ),
  })
  .superRefine(({ keys }, ctx) => {
    keys.forEach((entry, index) => {
      if (!entry.isOptional && entry.value.trim() === "") {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Value is required",
          path: ["keys", index, "value"],
        })
      }
    })
  })

type CreateSecretForm = z.infer<typeof createSecretSchema>

const ACTIONS_COLLAPSED_LIMIT = 10

export default function SecretCatalogDetailPage() {
  const params = useParams()
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const [showAllActions, setShowAllActions] = useState(false)

  const secretName = params?.secretName as string

  const {
    secretDefinitions,
    secretDefinitionsIsLoading,
    secretDefinitionsError,
  } = useSecretDefinitions(workspaceId)
  const { createSecret } = useWorkspaceSecrets(workspaceId)

  const secretDefinition = secretDefinitions?.find((s) => s.name === secretName)

  // Build initial keys from definition
  const initialKeys = [
    ...(secretDefinition?.keys || []).map((key) => ({
      key,
      value: "",
      isOptional: false,
    })),
    ...(secretDefinition?.optional_keys || []).map((key) => ({
      key,
      value: "",
      isOptional: true,
    })),
  ]

  const form = useForm<CreateSecretForm>({
    resolver: zodResolver(createSecretSchema),
    defaultValues: {
      name: secretName || "",
      description: "",
      environment: "default",
      keys: initialKeys,
    },
    values: {
      name: secretName || "",
      description: "",
      environment: "default",
      keys: initialKeys,
    },
  })

  const { fields } = useFieldArray({
    control: form.control,
    name: "keys",
  })

  const onSubmit = async (values: CreateSecretForm) => {
    // Filter out optional keys with empty values
    const filteredKeys = values.keys
      .filter((k) => !k.isOptional || k.value.trim() !== "")
      .map(({ key, value }) => ({ key, value }))

    if (filteredKeys.length === 0) {
      toast({
        title: "No keys provided",
        description: "Please provide at least one key value.",
        variant: "destructive",
      })
      return
    }

    const secret: SecretCreate = {
      name: values.name,
      description: values.description || undefined,
      environment: values.environment,
      type: "custom",
      keys: filteredKeys,
    }

    try {
      await createSecret(secret)
      toast({
        title: "Secret created",
        description: `Secret "${values.name}" has been created successfully.`,
      })
      router.push(`/workspaces/${workspaceId}/credentials`)
    } catch (error) {
      console.error("Failed to create secret:", error)
    }
  }

  if (secretDefinitionsIsLoading) {
    return <CenteredSpinner />
  }

  if (secretDefinitionsError) {
    return <div>Error: {secretDefinitionsError.message}</div>
  }

  if (!secretDefinition) {
    return (
      <div className="container mx-auto mb-20 mt-12 max-w-4xl p-6">
        <Link
          href={`/workspaces/${workspaceId}/credentials/catalog`}
          className="mb-4 inline-flex items-center text-sm text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="mr-1 size-4" />
          Go back
        </Link>
        <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
          Secret not found. It may have been removed or renamed.
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto mb-20 mt-12 max-w-4xl p-6">
      <Link
        href={`/workspaces/${workspaceId}/credentials/catalog`}
        className="mb-4 inline-flex items-center text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="mr-1 size-4" />
        Go back
      </Link>

      {/* Header */}
      <div className="mb-8 flex items-center gap-4">
        <SecretIcon secretName={secretDefinition.name} className="size-12" />
        <h1 className="text-3xl font-bold">{secretDefinition.name}</h1>
      </div>

      {/* Related Actions */}
      <div className="mb-8">
        <h2 className="mb-3 text-lg font-semibold">Related actions</h2>
        <div className="flex flex-wrap items-center gap-2">
          {(showAllActions
            ? secretDefinition.actions
            : secretDefinition.actions.slice(0, ACTIONS_COLLAPSED_LIMIT)
          ).map((action) => (
            <Badge key={action} variant="secondary" className="text-xs">
              {action}
            </Badge>
          ))}
          {secretDefinition.actions.length > ACTIONS_COLLAPSED_LIMIT && (
            <button
              type="button"
              onClick={() => setShowAllActions(!showAllActions)}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              {showAllActions ? "Collapse" : "View all"}
            </button>
          )}
        </div>
      </div>

      {/* Create Credential Form */}
      <div>
        <h2 className="mb-3 text-lg font-semibold">Create credential</h2>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input {...field} disabled className="bg-muted" />
                  </FormControl>
                  <FormDescription>
                    The secret name is pre-filled and cannot be changed.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description (optional)</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      placeholder="Add a description for this credential"
                      className="h-20 resize-none"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="environment"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Environment</FormLabel>
                  <FormControl>
                    <Input {...field} placeholder="default" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="space-y-3">
              <FormLabel>Keys</FormLabel>
              {fields.map((field, index) => (
                <FormField
                  key={field.id}
                  control={form.control}
                  name={`keys.${index}.value`}
                  render={({ field: valueField }) => (
                    <FormItem>
                      <div className="grid grid-cols-2 items-center gap-x-6 gap-y-2">
                        <div className="flex min-w-0 items-center gap-1">
                          <span
                            className="min-w-0 flex-1 truncate text-sm font-medium"
                            title={form.getValues(`keys.${index}.key`)}
                          >
                            {form.getValues(`keys.${index}.key`)}
                          </span>
                          {form.getValues(`keys.${index}.isOptional`) && (
                            <span className="shrink-0 text-xs text-muted-foreground">
                              (optional)
                            </span>
                          )}
                        </div>
                        <FormControl>
                          <Input
                            {...valueField}
                            type="password"
                            placeholder={`Enter ${form.getValues(`keys.${index}.key`)}`}
                            className="w-full"
                          />
                        </FormControl>
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ))}
            </div>

            <div className="flex justify-end pt-4">
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? (
                  <>
                    <Loader2 className="mr-2 size-4 animate-spin" />
                    Creating...
                  </>
                ) : (
                  "Create credential"
                )}
              </Button>
            </div>
          </form>
        </Form>
      </div>
    </div>
  )
}
