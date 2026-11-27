"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ImageIcon } from "lucide-react"
import { type ChangeEvent, useRef } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { BrandingSettingsUpdate } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useOrgBrandingSettings } from "@/hooks/use-branding"
import {
  DEFAULT_PRIMARY_COLOR,
  foregroundHslTriplet,
  HEX_COLOR_PATTERN,
} from "@/lib/branding"
import { cn } from "@/lib/utils"

const LOGO_MAX_BYTES = 256 * 1024
const LOGO_TYPES = ["image/png", "image/jpeg", "image/webp", "image/svg+xml"]

const hexColor = z
  .string()
  .refine((value) => value === "" || HEX_COLOR_PATTERN.test(value))

/** Keep up to six hex digits and store them with a leading `#`. */
function normalizeHexInput(raw: string): string {
  const digits = raw.replace(/[^0-9a-fA-F]/g, "").slice(0, 6)
  return digits ? `#${digits}` : ""
}

const brandingFormSchema = z.object({
  branding_logo: z.string().nullable(),
  branding_primary_color_light: hexColor,
  branding_primary_color_dark: hexColor,
  branding_terms_enabled: z.boolean(),
})

type BrandingFormValues = z.infer<typeof brandingFormSchema>

function previewColor(value: string): string {
  return HEX_COLOR_PATTERN.test(value) ? value : DEFAULT_PRIMARY_COLOR
}

function ColorField({
  form,
  name,
  label,
  description,
}: {
  form: ReturnType<typeof useForm<BrandingFormValues>>
  name: "branding_primary_color_light" | "branding_primary_color_dark"
  label: string
  description: string
}) {
  return (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => (
        <FormItem className="flex flex-row items-center justify-between gap-6 px-4 py-3">
          <div className="space-y-0.5">
            <FormLabel className="text-foreground">{label}</FormLabel>
            <FormDescription>{description}</FormDescription>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="size-5 shrink-0 rounded-full border"
              style={{ backgroundColor: previewColor(field.value) }}
              aria-hidden
            />
            <div className="flex h-8 w-28 items-center rounded-md border border-input pl-3 font-mono text-xs focus-within:ring-1 focus-within:ring-inset focus-within:ring-ring">
              <span className="text-muted-foreground" aria-hidden>
                #
              </span>
              <FormControl>
                <Input
                  {...field}
                  value={field.value.replace(/^#/, "")}
                  onChange={(event) =>
                    field.onChange(normalizeHexInput(event.target.value))
                  }
                  maxLength={7}
                  placeholder={DEFAULT_PRIMARY_COLOR.slice(1)}
                  spellCheck={false}
                  autoComplete="off"
                  className="h-full border-0 pl-0.5 font-mono text-xs focus-visible:ring-0"
                />
              </FormControl>
            </div>
          </div>
        </FormItem>
      )}
    />
  )
}

function BrandingPreview({
  mode,
  color,
  logo,
}: {
  mode: "light" | "dark"
  color: string
  logo: string | null
}) {
  const foreground = `hsl(${foregroundHslTriplet(color)})`
  return (
    <div
      className={cn(
        "flex flex-1 flex-col gap-4 rounded-lg border p-4",
        mode === "dark"
          ? "border-zinc-800 bg-zinc-950 text-zinc-100"
          : "border-zinc-200 bg-white text-zinc-900"
      )}
    >
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium capitalize">{mode}</span>
        <span className="font-mono opacity-60">{color}</span>
      </div>
      <div className="flex items-center gap-3">
        {logo ? (
          <img
            src={logo}
            alt=""
            className="h-8 w-auto max-w-24 object-contain"
          />
        ) : (
          <p className="text-sm font-semibold">Tracecat</p>
        )}
      </div>
      <div className="flex items-center gap-3">
        <span
          className="rounded-md px-3 py-1.5 text-xs font-medium"
          style={{ backgroundColor: color, color: foreground }}
        >
          Button
        </span>
        <span className="text-xs underline" style={{ color }}>
          Link
        </span>
        <span
          className="ml-auto size-4 rounded-full border-4"
          style={{ borderColor: color }}
          aria-hidden
        />
      </div>
    </div>
  )
}

/** Organization branding form: logo, primary colors, and terms notice. */
export function OrgSettingsBrandingForm() {
  const {
    brandingSettings,
    brandingSettingsIsLoading,
    brandingSettingsError,
    updateBrandingSettings,
    updateBrandingSettingsIsPending,
  } = useOrgBrandingSettings()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const form = useForm<BrandingFormValues>({
    resolver: zodResolver(brandingFormSchema),
    values: {
      branding_logo: brandingSettings?.branding_logo ?? null,
      branding_primary_color_light:
        brandingSettings?.branding_primary_color_light ?? "",
      branding_primary_color_dark:
        brandingSettings?.branding_primary_color_dark ?? "",
      branding_terms_enabled: brandingSettings?.branding_terms_enabled ?? true,
    },
  })
  const logo = form.watch("branding_logo")
  const rawLightColor = form.watch("branding_primary_color_light")
  const rawDarkColor = form.watch("branding_primary_color_dark")
  const lightColor = previewColor(rawLightColor)
  const darkColor = previewColor(rawDarkColor)
  const colorsComplete = [rawLightColor, rawDarkColor].every(
    (value) => value === "" || HEX_COLOR_PATTERN.test(value)
  )

  function handleLogoChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (!file) {
      return
    }
    if (!LOGO_TYPES.includes(file.type)) {
      toast({
        title: "Unsupported file",
        description: "Upload a PNG, JPEG, WebP, or SVG image.",
      })
      return
    }
    if (file.size > LOGO_MAX_BYTES) {
      toast({
        title: "Logo too large",
        description: "Logos must be 256 KB or smaller.",
      })
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === "string") {
        form.setValue("branding_logo", reader.result, { shouldDirty: true })
      }
    }
    reader.readAsDataURL(file)
  }

  async function onSubmit(data: BrandingFormValues) {
    try {
      const dirty = form.formState.dirtyFields
      const update: BrandingSettingsUpdate = {}
      if (dirty.branding_logo) {
        update.branding_logo = data.branding_logo
      }
      if (dirty.branding_primary_color_light) {
        update.branding_primary_color_light =
          data.branding_primary_color_light || null
      }
      if (dirty.branding_primary_color_dark) {
        update.branding_primary_color_dark =
          data.branding_primary_color_dark || null
      }
      if (dirty.branding_terms_enabled) {
        update.branding_terms_enabled = data.branding_terms_enabled
      }
      await updateBrandingSettings(update)
      toast({ title: "Branding saved" })
    } catch {
      toast({
        title: "Failed to save branding",
        description: "Check the logo and colors and try again.",
      })
    }
  }

  if (brandingSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (brandingSettingsError || !brandingSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading branding settings: ${brandingSettingsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <section className="space-y-3">
          <h3 className="text-sm font-medium">Logo</h3>
          <div className="flex items-center justify-between gap-6 rounded-lg border px-4 py-3">
            <div className="flex items-center gap-4">
              <div className="flex size-12 items-center justify-center rounded-md border bg-muted/40">
                {logo ? (
                  <img
                    src={logo}
                    alt="Organization logo"
                    className="max-h-10 max-w-10 object-contain"
                  />
                ) : (
                  <ImageIcon className="size-4 text-muted-foreground" />
                )}
              </div>
              <div className="space-y-0.5">
                <p className="text-sm font-medium">Organization logo</p>
                <p className="text-sm text-muted-foreground">
                  Shown on sign-in and sign-up in place of the Tracecat logo.
                  PNG, JPEG, WebP, or SVG, up to 256 KB.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {logo && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    form.setValue("branding_logo", null, { shouldDirty: true })
                  }
                >
                  Remove
                </Button>
              )}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                Upload
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept={LOGO_TYPES.join(",")}
                className="hidden"
                onChange={handleLogoChange}
              />
            </div>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-medium">Primary color</h3>
          <div className="divide-y rounded-lg border">
            <ColorField
              form={form}
              name="branding_primary_color_light"
              label="Light mode"
              description="Buttons, links, and focus rings in light mode."
            />
            <ColorField
              form={form}
              name="branding_primary_color_dark"
              label="Dark mode"
              description="Buttons, links, and focus rings in dark mode."
            />
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <BrandingPreview mode="light" color={lightColor} logo={logo} />
            <BrandingPreview mode="dark" color={darkColor} logo={logo} />
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-medium">Sign-in page</h3>
          <FormField
            control={form.control}
            name="branding_terms_enabled"
            render={({ field }) => (
              <FormItem className="flex flex-row items-center justify-between rounded-lg border px-4 py-3">
                <div className="space-y-0.5">
                  <FormLabel>Show terms and privacy</FormLabel>
                  <FormDescription>
                    Show the Terms of Service and Privacy Policy notice on
                    sign-in and sign-up.
                  </FormDescription>
                </div>
                <FormControl>
                  <Switch
                    checked={field.value}
                    onCheckedChange={field.onChange}
                  />
                </FormControl>
              </FormItem>
            )}
          />
        </section>

        <Button
          type="submit"
          size="sm"
          disabled={
            updateBrandingSettingsIsPending ||
            !form.formState.isDirty ||
            !colorsComplete
          }
        >
          {updateBrandingSettingsIsPending ? "Saving..." : "Save changes"}
        </Button>
      </form>
    </Form>
  )
}
