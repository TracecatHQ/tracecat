"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { ArrowLeftIcon } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { use, useEffect } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { useAdminOrganization } from "@/hooks/use-admin"

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
  is_active: z.boolean(),
})

type FormValues = z.infer<typeof formSchema>

export default function AdminOrganizationDetailPage({
  params,
}: {
  params: Promise<{ orgId: string }>
}) {
  const router = useRouter()
  const { orgId } = use(params)
  const { organization, isLoading, updateOrganization, updatePending } =
    useAdminOrganization(orgId)

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      slug: "",
      is_active: true,
    },
  })

  useEffect(() => {
    if (organization) {
      form.reset({
        name: organization.name,
        slug: organization.slug,
        is_active: organization.is_active,
      })
    }
  }, [organization, form])

  const onSubmit = async (values: FormValues) => {
    try {
      await updateOrganization(values)
      toast({
        title: "Organization updated",
        description: "Changes have been saved.",
      })
      router.push("/admin/organizations")
    } catch (error) {
      console.error("Failed to update organization", error)
      toast({
        title: "Failed to update organization",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  if (isLoading) {
    return <div className="text-center text-muted-foreground">Loading...</div>
  }

  if (!organization) {
    return (
      <div className="text-center text-muted-foreground">
        Organization not found
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/admin/organizations"
          className="flex items-center text-sm text-muted-foreground hover:text-foreground mb-4"
        >
          <ArrowLeftIcon className="mr-2 size-4" />
          Back to organizations
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">
          Edit organization
        </h1>
        <p className="text-muted-foreground">
          Update organization details for {organization.name}.
        </p>
      </div>

      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-6 max-w-lg"
        >
          <FormField
            control={form.control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Name</FormLabel>
                <FormControl>
                  <Input {...field} />
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
                  <Input {...field} />
                </FormControl>
                <FormDescription>
                  Unique identifier for the organization. Used in URLs.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="is_active"
            render={({ field }) => (
              <FormItem className="flex items-center gap-2 space-y-0">
                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={field.onChange}
                  />
                </FormControl>
                <FormLabel className="font-normal">Active</FormLabel>
                <FormDescription className="ml-4">
                  Inactive organizations cannot be accessed by users.
                </FormDescription>
              </FormItem>
            )}
          />
          <div className="flex gap-4">
            <Button type="submit" disabled={updatePending}>
              {updatePending ? "Saving..." : "Save changes"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push("/admin/organizations")}
            >
              Cancel
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}
