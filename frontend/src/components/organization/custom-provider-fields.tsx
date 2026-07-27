import { ChevronDown } from "lucide-react"
import type { UseFormReturn } from "react-hook-form"
import type { CustomProviderType } from "@/client"
import {
  type CustomProviderFormValues,
  typeSupportsCredentials,
} from "@/components/organization/custom-provider-form"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"

/**
 * Base URL field with type-aware helper text (LiteLLM gets a proxy hint).
 */
export function BaseUrlField({
  form,
  type,
}: {
  form: UseFormReturn<CustomProviderFormValues>
  type: CustomProviderType
}) {
  let placeholder = "https://gateway.example.com/v1"
  if (type === "ollama") {
    placeholder = "http://localhost:11434"
  } else if (type === "litellm") {
    placeholder = "http://localhost:4000"
  }

  return (
    <FormField
      control={form.control}
      name="baseUrl"
      render={({ field }) => (
        <FormItem>
          <FormLabel>Base URL</FormLabel>
          <FormControl>
            <Input {...field} placeholder={placeholder} />
          </FormControl>
          {type === "litellm" ? (
            <FormDescription>
              The LiteLLM proxy base URL. Either the root or a{" "}
              <code className="bg-muted rounded px-1 py-0.5 font-mono">
                /v1
              </code>{" "}
              suffix is accepted.
            </FormDescription>
          ) : null}
          {type === "ollama" ? (
            <FormDescription>
              The Ollama server root. A trailing{" "}
              <code className="bg-muted rounded px-1 py-0.5 font-mono">
                /v1
              </code>{" "}
              is optional and handled automatically; models are discovered from{" "}
              <code className="bg-muted rounded px-1 py-0.5 font-mono">
                /api/tags
              </code>
              .
            </FormDescription>
          ) : null}
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

/**
 * Credential fields (auth header + value). Hidden entirely for Ollama, which
 * needs no API key.
 */
export function CredentialFields({
  form,
  type,
  isEdit,
}: {
  form: UseFormReturn<CustomProviderFormValues>
  type: CustomProviderType
  isEdit: boolean
}) {
  if (!typeSupportsCredentials(type)) {
    return null
  }

  return (
    <div className="space-y-2">
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField
          control={form.control}
          name="apiKeyHeader"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Auth header</FormLabel>
              <FormControl>
                <Input {...field} placeholder="Authorization" />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="apiKey"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Auth value</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="password"
                  placeholder={
                    isEdit ? "Leave blank to keep saved value" : "sk-••••••••"
                  }
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
      <p className="text-muted-foreground text-xs">
        Defaults to{" "}
        <code className="bg-muted rounded px-1 py-0.5 font-mono">
          Authorization: Bearer &lt;value&gt;
        </code>{" "}
        if no header is set.
      </p>
    </div>
  )
}

/**
 * Surface the Advanced section is rendered on. The create wizard hides the
 * passthrough control for litellm/ollama (silently created with the prefilled
 * passthrough=true); the edit dialog always shows it.
 */
export type CustomProviderSurface = "wizard" | "edit"

/**
 * Whether the passthrough toggle is visible for this type on this surface.
 *
 * Hidden only in the create wizard for litellm/ollama, which are silently
 * created with passthrough=true (the prefilled form value is still submitted
 * verbatim). Visible everywhere else, including the edit dialog for all types.
 */
function isPassthroughVisible(
  type: CustomProviderType,
  surface: CustomProviderSurface
): boolean {
  if (surface === "wizard" && (type === "litellm" || type === "ollama")) {
    return false
  }
  return true
}

/**
 * Passthrough toggle shown inside the Advanced section. Rendered for every
 * provider type; the wizard prefills a per-type default but the user is free
 * to change it.
 */
function PassthroughField({
  form,
}: {
  form: UseFormReturn<CustomProviderFormValues>
}) {
  return (
    <FormField
      control={form.control}
      name="passthrough"
      render={({ field }) => (
        <FormItem className="flex items-center justify-between gap-4 rounded-md border p-3">
          <div className="space-y-1">
            <FormLabel className="text-sm">Passthrough mode</FormLabel>
            <FormDescription>
              Recommended for bring-your-own gateways (LiteLLM, vLLM, etc.).
              Skips Tracecat&apos;s transforms and forwards requests directly to
              your endpoint.
            </FormDescription>
          </div>
          <FormControl>
            <Switch checked={field.value} onCheckedChange={field.onChange} />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

/**
 * Additional static headers JSON field. Rendered inside the Advanced section.
 * The API-key reference is dropped for Ollama, which has no API key field.
 */
function CustomHeadersField({
  form,
  type,
}: {
  form: UseFormReturn<CustomProviderFormValues>
  type: CustomProviderType
}) {
  const nonAuthClause =
    type === "ollama"
      ? "Use this for extra non-auth headers."
      : "Use this for non-auth headers not covered by the API key above."
  return (
    <FormField
      control={form.control}
      name="customHeadersJson"
      render={({ field }) => (
        <FormItem>
          <FormLabel>Additional headers</FormLabel>
          <FormControl>
            <Textarea
              {...field}
              className="min-h-28 font-mono text-xs"
              placeholder='{"X-Custom-Header":"value"}'
            />
          </FormControl>
          <FormDescription>
            Optional JSON object of extra static headers sent on every request.
            {` ${nonAuthClause} `}
            Saving new JSON replaces the saved value.
          </FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

/**
 * Collapsible "Advanced" section. Contains, in order, the passthrough toggle
 * followed by the additional-headers JSON field. The passthrough toggle is
 * hidden in the create wizard for litellm/ollama (see {@link isPassthroughVisible});
 * on the edit dialog it is always shown. Open/close state is owned by the
 * caller so error-driven auto-open (for the headers JSON) can force it open.
 */
export function AdvancedSection({
  form,
  type,
  open,
  onOpenChange,
  surface,
}: {
  form: UseFormReturn<CustomProviderFormValues>
  type: CustomProviderType
  open: boolean
  onOpenChange: (open: boolean) => void
  surface: CustomProviderSurface
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange}>
      <CollapsibleTrigger className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-sm [&[data-state=open]>svg]:rotate-180">
        <ChevronDown className="size-4 transition-transform duration-200" />
        Advanced
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="space-y-4 pt-3">
          {isPassthroughVisible(type, surface) ? (
            <PassthroughField form={form} />
          ) : null}
          <CustomHeadersField form={form} type={type} />
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
