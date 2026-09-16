"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { CloudIcon, PlusCircle, Trash2Icon } from "lucide-react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type { AwsSecretKeyMapping, AwsSecretReferenceCreate } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { DialogFooter } from "@/components/ui/dialog"
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
import {
  useAuthorizedSecretStores,
  useAwsSecretReferences,
} from "@/hooks/use-secret-stores"
import { useWorkspaceId } from "@/providers/workspace-id"

const SECRET_ID_REGEX =
  /^(?:arn:aws(?:-[a-z]+)*:secretsmanager:[a-z0-9-]+:\d{12}:secret:.+|[A-Za-z0-9/_+=.@-]{1,512})$/
const KEY_REGEX = /^[A-Za-z_][A-Za-z0-9_]*$/

const awsReferenceSchema = z
  .object({
    name: z
      .string()
      .min(1, "Name is required")
      .regex(/^[a-z0-9_]+$/, "Use lowercase letters, digits, and underscores"),
    description: z.string().max(255).default(""),
    environment: z.string().default(""),
    store_id: z.string().min(1, "Select an authorized store"),
    remote_reference: z
      .string()
      .regex(SECRET_ID_REGEX, "Enter a Secrets Manager secret name or ARN"),
    mode: z.enum(["whole_string", "json"]),
    whole_string_key: z.string().default(""),
    fields: z
      .array(
        z.object({
          key: z.string(),
          field: z.string(),
        })
      )
      .default([]),
  })
  .superRefine((values, ctx) => {
    if (values.mode === "whole_string") {
      if (!KEY_REGEX.test(values.whole_string_key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["whole_string_key"],
          message: "Declare one output key name",
        })
      }
      return
    }
    if (values.fields.length === 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["fields"],
        message: "Declare at least one JSON field",
      })
    }
    const seen = new Set<string>()
    values.fields.forEach((entry, index) => {
      if (!KEY_REGEX.test(entry.key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["fields", index, "key"],
          message: "Invalid key name",
        })
      }
      if (entry.field.trim() === "") {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["fields", index, "field"],
          message: "JSON field is required",
        })
      }
      if (seen.has(entry.key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["fields", index, "key"],
          message: "Output keys must be unique",
        })
      }
      seen.add(entry.key)
    })
  })

type AwsReferenceForm = z.infer<typeof awsReferenceSchema>

/**
 * Converts form values into the API key mapping. Only key names and JSON
 * field selectors are sent; the remote value is never read by the browser.
 */
export function buildKeyMapping(values: AwsReferenceForm): AwsSecretKeyMapping {
  if (values.mode === "whole_string") {
    return { mode: "whole_string", keys: [values.whole_string_key.trim()] }
  }
  return {
    mode: "json",
    fields: values.fields.map((entry) => ({
      key: entry.key.trim(),
      field: entry.field.trim(),
    })),
  }
}

interface CreateAwsSecretReferenceFormProps {
  /** Pre-filled secret name (e.g. from an integration template). */
  initialName?: string
  /** Declared keys to seed the JSON mapping with. */
  initialKeys?: string[]
  onCreated: () => void
}

/**
 * Form for creating a workspace custom secret whose values live in AWS
 * Secrets Manager. Collects a store, a secret name or ARN, and a key mapping.
 * There is intentionally no value editor, value preview, or rotation control.
 */
export function CreateAwsSecretReferenceForm({
  initialName = "",
  initialKeys = [],
  onCreated,
}: CreateAwsSecretReferenceFormProps) {
  const workspaceId = useWorkspaceId()
  const { stores, isLoading: storesLoading } =
    useAuthorizedSecretStores(workspaceId)
  const { createReference } = useAwsSecretReferences(workspaceId)

  const methods = useForm<AwsReferenceForm>({
    resolver: zodResolver(awsReferenceSchema),
    defaultValues: {
      name: initialName,
      description: "",
      environment: "",
      store_id: "",
      remote_reference: "",
      mode: initialKeys.length > 1 ? "json" : "whole_string",
      whole_string_key: initialKeys.length === 1 ? initialKeys[0] : "",
      fields: initialKeys.map((key) => ({ key, field: "" })),
    },
  })
  const { control, register } = methods
  const mode = methods.watch("mode")
  const { fields, append, remove } = useFieldArray({ control, name: "fields" })

  const enabledStores = (stores ?? []).filter((store) => store.enabled)

  async function onSubmit(values: AwsReferenceForm) {
    const params: AwsSecretReferenceCreate = {
      name: values.name,
      description: values.description || null,
      environment: values.environment.trim() || "default",
      store_id: values.store_id,
      remote_reference: values.remote_reference.trim(),
      key_mapping: buildKeyMapping(values),
    }
    try {
      await createReference(params)
      onCreated()
    } catch {
      // The mutation hook already surfaced a toast.
    }
  }

  return (
    <Form {...methods}>
      <form
        onSubmit={methods.handleSubmit(onSubmit)}
        className="flex flex-col flex-1 min-h-0"
        data-testid="aws-secret-reference-form"
      >
        <div className="space-y-4 overflow-y-auto flex-1 py-2 px-1">
          {!storesLoading && enabledStores.length === 0 && (
            <Alert>
              <CloudIcon className="size-4" />
              <AlertTitle>No authorized AWS stores</AlertTitle>
              <AlertDescription>
                An organization admin must add an AWS Secrets Manager store and
                authorize this workspace before you can reference secrets from
                it.
              </AlertDescription>
            </Alert>
          )}
          <FormField
            control={control}
            name="name"
            render={() => (
              <FormItem>
                <FormLabel className="text-sm">Name</FormLabel>
                <FormDescription className="text-sm">
                  Referenced as <code>SECRETS.&lt;name&gt;.&lt;key&gt;</code>.
                </FormDescription>
                <FormControl>
                  <Input
                    className="text-sm"
                    placeholder="Name (snake case)"
                    readOnly={Boolean(initialName)}
                    {...register("name")}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="description"
            render={() => (
              <FormItem>
                <FormLabel className="text-sm">Description</FormLabel>
                <FormControl>
                  <Input
                    className="text-sm"
                    placeholder="Description"
                    {...register("description")}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="environment"
            render={() => (
              <FormItem>
                <FormLabel className="text-sm">Environment</FormLabel>
                <FormControl>
                  <Input
                    className="text-sm"
                    placeholder='Default environment: "default"'
                    {...register("environment")}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="store_id"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-sm">AWS secret store</FormLabel>
                <FormDescription className="text-sm">
                  Only stores authorized for this workspace are listed.
                </FormDescription>
                <Select onValueChange={field.onChange} value={field.value}>
                  <FormControl>
                    <SelectTrigger className="text-sm">
                      <SelectValue placeholder="Select a store" />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {enabledStores.map((store) => (
                      <SelectItem key={store.id} value={store.id}>
                        {store.name}{" "}
                        <span className="text-muted-foreground">
                          ({store.region})
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="remote_reference"
            render={() => (
              <FormItem>
                <FormLabel className="text-sm">Secret name or ARN</FormLabel>
                <FormDescription className="text-sm">
                  The secret name as shown in the AWS console (looked up in the
                  store's region) or its full ARN.
                </FormDescription>
                <FormControl>
                  <Input
                    className="font-mono text-xs"
                    placeholder="prod/app/api-key"
                    {...register("remote_reference")}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={control}
            name="mode"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-sm">Value mapping</FormLabel>
                <FormDescription className="text-sm">
                  How the remote SecretString maps onto secret keys.
                </FormDescription>
                <Select onValueChange={field.onChange} value={field.value}>
                  <FormControl>
                    <SelectTrigger className="text-sm">
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    <SelectItem value="whole_string">
                      Whole string → one key
                    </SelectItem>
                    <SelectItem value="json">
                      JSON object → selected fields
                    </SelectItem>
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          {mode === "whole_string" && (
            <FormField
              control={control}
              name="whole_string_key"
              render={() => (
                <FormItem>
                  <FormLabel className="text-sm">Output key</FormLabel>
                  <FormControl>
                    <Input
                      className="text-sm"
                      placeholder="API_TOKEN"
                      {...register("whole_string_key")}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          )}
          {mode === "json" && (
            <FormField
              control={control}
              name="fields"
              render={() => (
                <FormItem>
                  <FormLabel className="text-sm">JSON fields</FormLabel>
                  <FormDescription className="text-sm">
                    Map top-level string fields to output keys.
                  </FormDescription>
                  <div className="space-y-2">
                    {fields.map((entry, index) => (
                      <div key={entry.id} className="flex items-start gap-2">
                        <FormField
                          control={control}
                          name={`fields.${index}.key`}
                          render={() => (
                            <FormItem className="flex-1">
                              <FormControl>
                                <Input
                                  className="text-sm"
                                  placeholder="Output key"
                                  {...register(`fields.${index}.key`)}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={control}
                          name={`fields.${index}.field`}
                          render={() => (
                            <FormItem className="flex-1">
                              <FormControl>
                                <Input
                                  className="text-sm"
                                  placeholder="JSON field"
                                  {...register(`fields.${index}.field`)}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label="Remove field"
                          onClick={() => remove(index)}
                        >
                          <Trash2Icon className="size-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => append({ key: "", field: "" })}
                  >
                    <PlusCircle className="mr-2 size-4" />
                    Add field
                  </Button>
                  <FormMessage />
                </FormItem>
              )}
            />
          )}
        </div>
        <DialogFooter className="flex-shrink-0 pt-4">
          <Button
            className="ml-auto"
            type="submit"
            disabled={enabledStores.length === 0}
          >
            <CloudIcon className="mr-2 size-4" />
            Save reference
          </Button>
        </DialogFooter>
      </form>
    </Form>
  )
}
