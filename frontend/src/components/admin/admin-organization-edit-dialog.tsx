"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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

interface AdminOrganizationEditDialogProps {
  orgId: string
  trigger: React.ReactNode
}

export function AdminOrganizationEditDialog({
  orgId,
  trigger,
}: AdminOrganizationEditDialogProps) {
  const [open, setOpen] = useState(false)
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
    if (organization && open) {
      form.reset({
        name: organization.name,
        slug: organization.slug,
        is_active: organization.is_active,
      })
    }
  }, [organization, form, open])

  const onSubmit = async (values: FormValues) => {
    try {
      await updateOrganization(values)
      toast({
        title: "Organization updated",
        description: "Changes have been saved.",
      })
      setOpen(false)
    } catch (error) {
      console.error("Failed to update organization", error)
      toast({
        title: "Failed to update organization",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit organization</DialogTitle>
          <DialogDescription>
            Update organization details
            {organization ? ` for ${organization.name}.` : "."}
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="py-8 text-center text-muted-foreground">
            Loading...
          </div>
        ) : !organization ? (
          <div className="py-8 text-center text-muted-foreground">
            Organization not found.
          </div>
        ) : (
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={updatePending}>
                  {updatePending ? "Saving..." : "Save changes"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  )
}
