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
  trigger?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function AdminOrganizationEditDialog({
  orgId,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: AdminOrganizationEditDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const [disableConfirmation, setDisableConfirmation] = useState("")
  const isControlled = controlledOpen !== undefined
  const dialogOpen = isControlled ? controlledOpen : internalOpen
  const setDialogOpen = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
  }
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
    if (organization && dialogOpen) {
      setDisableConfirmation("")
      form.reset({
        name: organization.name,
        slug: organization.slug,
        is_active: organization.is_active,
      })
    }
  }, [organization, form, dialogOpen])

  const isDisablingOrganization =
    organization?.is_active === true && form.watch("is_active") === false
  const isDisableConfirmationValid =
    !isDisablingOrganization || disableConfirmation === organization?.name

  const onSubmit = async (values: FormValues) => {
    try {
      await updateOrganization(values)
      toast({
        title: "Organization updated",
        description: "Changes have been saved.",
      })
      setDialogOpen(false)
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
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
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
              {isDisablingOrganization ? (
                <div className="space-y-2 rounded-md border border-destructive/30 p-3">
                  <p className="text-sm text-muted-foreground">
                    Type{" "}
                    <span className="font-medium">{organization.name}</span> to
                    disable this organization.
                  </p>
                  <Input
                    value={disableConfirmation}
                    onChange={(event) =>
                      setDisableConfirmation(event.target.value)
                    }
                    placeholder={organization.name}
                    autoComplete="off"
                  />
                </div>
              ) : null}
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={updatePending || !isDisableConfirmationValid}
                >
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
