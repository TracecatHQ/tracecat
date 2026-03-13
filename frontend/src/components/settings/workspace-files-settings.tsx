"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { RefreshCwIcon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
import { CustomTagInput } from "@/components/tags-input"
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
import { Switch } from "@/components/ui/switch"
import { useWorkspaceSettings } from "@/lib/hooks"

const filesSettingsSchema = z.object({
  allowed_attachment_extensions: z
    .array(
      z.object({
        id: z.string(),
        text: z.string().min(1, "Cannot be empty"),
      })
    )
    .optional(),
  allowed_attachment_mime_types: z
    .array(
      z.object({
        id: z.string(),
        text: z.string().min(1, "Cannot be empty"),
      })
    )
    .optional(),
  validate_attachment_magic_number: z.boolean().optional(),
})

type FilesSettingsForm = z.infer<typeof filesSettingsSchema>

interface FilesSettingsUpdateOptions {
  inheritAttachmentExtensions?: boolean
  inheritAttachmentMimeTypes?: boolean
}

export function buildFilesSettingsUpdate(
  values: FilesSettingsForm,
  options: FilesSettingsUpdateOptions = {}
) {
  return {
    allowed_attachment_extensions: options.inheritAttachmentExtensions
      ? null
      : values.allowed_attachment_extensions === undefined
        ? undefined
        : values.allowed_attachment_extensions.length > 0
          ? values.allowed_attachment_extensions.map((ext) => ext.text)
          : null,
    allowed_attachment_mime_types: options.inheritAttachmentMimeTypes
      ? null
      : values.allowed_attachment_mime_types === undefined
        ? undefined
        : values.allowed_attachment_mime_types.length > 0
          ? values.allowed_attachment_mime_types.map((mime) => mime.text)
          : null,
    validate_attachment_magic_number: values.validate_attachment_magic_number,
  }
}

interface WorkspaceFilesSettingsProps {
  workspace: WorkspaceRead
}

export function WorkspaceFilesSettings({
  workspace,
}: WorkspaceFilesSettingsProps) {
  const systemDefaultExtensions =
    workspace.settings?.effective_allowed_attachment_extensions || []
  const systemDefaultMimeTypes =
    workspace.settings?.effective_allowed_attachment_mime_types || []
  const [inheritAttachmentExtensions, setInheritAttachmentExtensions] =
    useState(false)
  const [inheritAttachmentMimeTypes, setInheritAttachmentMimeTypes] =
    useState(false)

  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)

  const form = useForm<FilesSettingsForm>({
    resolver: zodResolver(filesSettingsSchema),
    mode: "onChange",
    defaultValues: {
      allowed_attachment_extensions: workspace.settings
        ?.allowed_attachment_extensions?.length
        ? workspace.settings.allowed_attachment_extensions.map(
            (ext, index) => ({
              id: `ext-${index}`,
              text: ext,
            })
          )
        : undefined,
      allowed_attachment_mime_types: workspace.settings
        ?.allowed_attachment_mime_types?.length
        ? workspace.settings.allowed_attachment_mime_types.map(
            (mime, index) => ({
              id: `mime-${index}`,
              text: mime,
            })
          )
        : undefined,
      validate_attachment_magic_number:
        workspace.settings?.validate_attachment_magic_number ?? true,
    },
  })

  async function onSubmit(values: FilesSettingsForm) {
    await updateWorkspace({
      settings: buildFilesSettingsUpdate(values, {
        inheritAttachmentExtensions,
        inheritAttachmentMimeTypes,
      }),
    })
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        <FormField
          control={form.control}
          name="allowed_attachment_extensions"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center justify-between">
                <FormLabel>Allowed file extensions</FormLabel>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setInheritAttachmentExtensions(true)
                    field.onChange(
                      systemDefaultExtensions.map((ext, index) => ({
                        id: `ext-default-${index}`,
                        text: ext,
                      }))
                    )
                  }}
                  className="h-auto p-1"
                >
                  <RefreshCwIcon className="size-3" />
                </Button>
              </div>
              <FormControl>
                <CustomTagInput
                  {...field}
                  placeholder="Enter an extension..."
                  tags={field.value || []}
                  setTags={(tags) => {
                    setInheritAttachmentExtensions(false)
                    field.onChange(tags)
                  }}
                />
              </FormControl>
              <FormDescription>
                Add file extensions that users can upload as attachments (e.g.,
                .pdf, .docx, .png)
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="allowed_attachment_mime_types"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center justify-between">
                <FormLabel>Allowed MIME types</FormLabel>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setInheritAttachmentMimeTypes(true)
                    field.onChange(
                      systemDefaultMimeTypes.map((mime, index) => ({
                        id: `mime-default-${index}`,
                        text: mime,
                      }))
                    )
                  }}
                  className="h-auto p-1"
                >
                  <RefreshCwIcon className="size-3" />
                </Button>
              </div>
              <FormControl>
                <CustomTagInput
                  {...field}
                  placeholder="Enter a MIME type..."
                  tags={field.value || []}
                  setTags={(tags) => {
                    setInheritAttachmentMimeTypes(false)
                    field.onChange(tags)
                  }}
                />
              </FormControl>
              <FormDescription>
                Add MIME types that are allowed for attachments (e.g.,
                application/pdf, image/jpeg)
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="validate_attachment_magic_number"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Validate file content</FormLabel>
                <FormDescription>
                  Verify that uploaded files match their declared type by
                  checking file signatures. Disabling this may allow malicious
                  files disguised as other formats.
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

        <Button type="submit" disabled={isUpdating} size="sm">
          {isUpdating ? "Saving..." : "Save"}
        </Button>
      </form>
    </Form>
  )
}
