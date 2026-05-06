"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { PlusIcon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
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
import { toast } from "@/components/ui/use-toast"
import { useAdminOrganizations } from "@/hooks/use-admin"
import { useAppInfo } from "@/lib/hooks"

const formSchema = z.object({
  name: z.string().min(1, "Name is required").max(100, "Name is too long"),
  slug: z
    .string()
    .min(1, "Slug is required")
    .max(50, "Slug is too long")
    .regex(
      /^[a-z0-9-]+$/,
      "Slug must contain only lowercase letters, numbers, and hyphens"
    ),
})

type FormValues = z.infer<typeof formSchema>

export function CreateOrganizationDialog() {
  const [open, setOpen] = useState(false)
  const { appInfo, appInfoIsLoading } = useAppInfo()
  const multiTenantEnabled = appInfo?.ee_multi_tenant === true
  const createDisabled = appInfoIsLoading || !multiTenantEnabled
  const { createOrganization, createPending } = useAdminOrganizations({
    enabled: false,
  })

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      slug: "",
    },
  })

  const onSubmit = async (values: FormValues) => {
    if (!multiTenantEnabled) {
      toast({
        title: "Organization creation is disabled",
        description: "Enable multi-tenant mode to create new organizations.",
        variant: "destructive",
      })
      return
    }

    try {
      await createOrganization(values)
      toast({
        title: "Organization created",
        description: `${values.name} has been created successfully.`,
      })
      form.reset()
      setOpen(false)
    } catch (error) {
      console.error("Failed to create organization", error)
      toast({
        title: "Failed to create organization",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  // Auto-generate slug from name
  const handleNameChange = (name: string) => {
    const slug = name
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/[^a-z0-9-]/g, "")
    form.setValue("slug", slug)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          disabled={createDisabled}
          title={
            createDisabled
              ? "Enable multi-tenant mode to create new organizations."
              : undefined
          }
        >
          <PlusIcon className="mr-2 size-4" />
          New organization
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create organization</DialogTitle>
          <DialogDescription>
            Create a new organization on the platform.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Acme Inc"
                      {...field}
                      onChange={(e) => {
                        field.onChange(e)
                        handleNameChange(e.target.value)
                      }}
                    />
                  </FormControl>
                  <FormDescription>
                    The display name of the organization.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="slug"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Slug</FormLabel>
                  <FormControl>
                    <Input placeholder="acme-inc" {...field} />
                  </FormControl>
                  <FormDescription>
                    Unique identifier for the organization. Used in URLs.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={createPending || createDisabled}>
                {createPending ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
