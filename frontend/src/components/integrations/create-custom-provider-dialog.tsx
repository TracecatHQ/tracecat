"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2, Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { MultiTagCommandInput } from "@/components/tags-input"
import { Button, type ButtonProps } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
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
import { useCreateCustomProvider } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const formSchema = z.object({
  name: z
    .string()
    .trim()
    .min(3, { message: "Name must be at least 3 characters long" })
    .max(120, { message: "Name must be 120 characters or fewer" }),
  description: z
    .string()
    .trim()
    .max(512, { message: "Description must be 512 characters or fewer" })
    .optional()
    .or(z.literal("")),
  grant_type: z.enum([
    "authorization_code",
    "client_credentials",
    "jwt_bearer",
  ]),
  client_id: z
    .string()
    .trim()
    .min(1, { message: "Client ID is required" })
    .max(512, { message: "Client ID must be 512 characters or fewer" }),
  client_secret: z
    .string()
    .trim()
    .max(16384, { message: "Client secret is unexpectedly long" })
    .optional()
    .or(z.literal("")),
  authorization_endpoint: z
    .string()
    .trim()
    .url({ message: "Enter a valid HTTPS URL" })
    .refine((value) => value.toLowerCase().startsWith("https://"), {
      message: "Authorization endpoint must use HTTPS",
    })
    .optional()
    .or(z.literal("")),
  token_endpoint: z
    .string()
    .trim()
    .url({ message: "Enter a valid HTTPS URL" })
    .refine((value) => value.toLowerCase().startsWith("https://"), {
      message: "Token endpoint must use HTTPS",
    }),
  scopes: z.array(z.string().trim().min(1)).optional(),
})

type CustomProviderFormValues = z.infer<typeof formSchema>

const DEFAULT_VALUES: CustomProviderFormValues = {
  name: "",
  description: "",
  grant_type: "authorization_code",
  client_id: "",
  client_secret: "",
  authorization_endpoint: "",
  token_endpoint: "",
  scopes: [],
}

const GRANT_OPTIONS = [
  {
    value: "authorization_code" as const,
    title: "Delegated access",
    description: "Users authorize access via OAuth login.",
  },
  {
    value: "client_credentials" as const,
    title: "Client credentials",
    description: "Server-to-server access with tokens.",
  },
  {
    value: "jwt_bearer" as const,
    title: "Private key JWT",
    description: "Service app with private key authentication.",
  },
]

export function CreateCustomProviderDialog({
  triggerProps,
  onOpenChange,
  open: controlledOpen,
  hideTrigger = false,
}: {
  triggerProps?: ButtonProps
  onOpenChange?: (open: boolean) => void
  open?: boolean
  hideTrigger?: boolean
}) {
  const workspaceId = useWorkspaceId()
  const { createCustomProvider, createCustomProviderIsPending } =
    useCreateCustomProvider(workspaceId)
  const [internalOpen, setInternalOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const { className: triggerClassName, ...restTriggerProps } =
    triggerProps ?? {}

  const form = useForm<CustomProviderFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: DEFAULT_VALUES,
  })

  const grantType = form.watch("grant_type")

  const resetForm = () => {
    form.reset(DEFAULT_VALUES)
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (controlledOpen === undefined) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
    if (!nextOpen) {
      resetForm()
    }
  }

  const onSubmit = async (values: CustomProviderFormValues) => {
    await createCustomProvider({
      name: values.name,
      description: values.description?.trim() || undefined,
      grant_type: values.grant_type,
      client_id: values.client_id,
      client_secret: values.client_secret?.trim() || undefined,
      authorization_endpoint:
        values.authorization_endpoint?.trim() || undefined,
      token_endpoint: values.token_endpoint,
      scopes: values.scopes ?? [],
    })
    handleOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {!hideTrigger && (
        <DialogTrigger asChild>
          <Button
            size="sm"
            variant="outline"
            className={cn("h-7 bg-white", triggerClassName)}
            {...restTriggerProps}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add OAuth provider
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Add custom OAuth provider</DialogTitle>
          <DialogDescription>
            Configure a custom OAuth provider using client credentials or
            delegated authorization.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            className="space-y-5 overflow-y-auto px-1"
            onSubmit={form.handleSubmit(onSubmit)}
            noValidate
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Provider name</FormLabel>
                  <FormControl>
                    <Input placeholder="My Security Platform" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder="Optional description for this provider"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription className="text-xs">
                    Appears in the integrations list for this workspace.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="grant_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Grant type</FormLabel>
                  <FormControl>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select grant type">
                          {field.value
                            ? GRANT_OPTIONS.find(
                                (opt) => opt.value === field.value
                              )?.title
                            : null}
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {GRANT_OPTIONS.map((option) => (
                          <SelectItem
                            key={option.value}
                            value={option.value}
                            textValue={option.title}
                          >
                            <div className="flex flex-col gap-1">
                              <span className="text-sm font-medium">
                                {option.title}
                              </span>
                              <span className="text-xs text-muted-foreground">
                                {option.description}
                              </span>
                            </div>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormControl>
                  <FormDescription className="text-xs">
                    Choose how Tracecat authenticates with this provider.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="client_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Client ID</FormLabel>
                    <FormControl>
                      <Input placeholder="Client ID" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="client_secret"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {grantType === "jwt_bearer"
                        ? "Private key (PEM)"
                        : "Client secret"}{" "}
                      {grantType === "authorization_code" && (
                        <span className="text-xs text-muted-foreground">
                          (optional)
                        </span>
                      )}
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={
                          grantType === "jwt_bearer"
                            ? "-----BEGIN RSA PRIVATE KEY-----"
                            : "Client secret"
                        }
                        type="password"
                        autoComplete="new-password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            {grantType !== "jwt_bearer" && (
              <FormField
                control={form.control}
                name="authorization_endpoint"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Authorization endpoint</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="https://example.com/oauth2/authorize"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}
            <FormField
              control={form.control}
              name="token_endpoint"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Token endpoint</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="https://example.com/oauth2/token"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="scopes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Scopes</FormLabel>
                  <FormControl>
                    <MultiTagCommandInput
                      value={field.value ?? []}
                      onChange={field.onChange}
                      suggestions={[]}
                      searchKeys={["label", "value"]}
                      allowCustomTags
                      placeholder="Add scopes"
                    />
                  </FormControl>
                  <FormDescription className="text-xs">
                    Provide the OAuth scopes to request by default.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter className="flex flex-col gap-2 sm:flex-row">
              <Button
                type="button"
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={createCustomProviderIsPending}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                className="gap-2"
                disabled={createCustomProviderIsPending}
              >
                {createCustomProviderIsPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Save provider
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
