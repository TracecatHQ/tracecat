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
import { useAdminTier } from "@/hooks/use-admin"

const formSchema = z.object({
  display_name: z
    .string()
    .min(1, "Name is required")
    .max(50, "Name is too long"),
  max_concurrent_workflows: z.coerce
    .number()
    .int()
    .positive()
    .optional()
    .nullable(),
  max_action_executions_per_workflow: z.coerce
    .number()
    .int()
    .positive()
    .optional()
    .nullable(),
  max_concurrent_actions: z.coerce
    .number()
    .int()
    .positive()
    .optional()
    .nullable(),
  api_rate_limit: z.coerce.number().int().positive().optional().nullable(),
  api_burst_capacity: z.coerce.number().int().positive().optional().nullable(),
  is_default: z.boolean(),
  is_active: z.boolean(),
  sort_order: z.coerce.number().int().min(0),
})

type FormValues = z.infer<typeof formSchema>

interface AdminTierEditDialogProps {
  tierId: string
  trigger?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function AdminTierEditDialog({
  tierId,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: AdminTierEditDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const isControlled = controlledOpen !== undefined
  const dialogOpen = isControlled ? controlledOpen : internalOpen
  const setDialogOpen = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
  }
  const { tier, isLoading, updateTier, updatePending } = useAdminTier(tierId)

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      display_name: "",
      is_default: false,
      is_active: true,
      sort_order: 0,
    },
  })

  useEffect(() => {
    if (tier && dialogOpen) {
      form.reset({
        display_name: tier.display_name,
        max_concurrent_workflows: tier.max_concurrent_workflows,
        max_action_executions_per_workflow:
          tier.max_action_executions_per_workflow,
        max_concurrent_actions: tier.max_concurrent_actions,
        api_rate_limit: tier.api_rate_limit,
        api_burst_capacity: tier.api_burst_capacity,
        is_default: tier.is_default,
        is_active: tier.is_active,
        sort_order: tier.sort_order,
      })
    }
  }, [tier, form, dialogOpen])

  const onSubmit = async (values: FormValues) => {
    try {
      await updateTier({
        display_name: values.display_name,
        max_concurrent_workflows: values.max_concurrent_workflows ?? null,
        max_action_executions_per_workflow:
          values.max_action_executions_per_workflow ?? null,
        max_concurrent_actions: values.max_concurrent_actions ?? null,
        api_rate_limit: values.api_rate_limit ?? null,
        api_burst_capacity: values.api_burst_capacity ?? null,
        is_default: values.is_default,
        is_active: values.is_active,
        sort_order: values.sort_order,
      })
      toast({
        title: "Tier updated",
        description: "Changes have been saved.",
      })
      setDialogOpen(false)
    } catch (error) {
      console.error("Failed to update tier", error)
      toast({
        title: "Failed to update tier",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit tier</DialogTitle>
          <DialogDescription>
            Update tier details{tier ? ` for ${tier.display_name}.` : "."}
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="py-8 text-center text-muted-foreground">
            Loading...
          </div>
        ) : !tier ? (
          <div className="py-8 text-center text-muted-foreground">
            Tier not found.
          </div>
        ) : (
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="display_name"
                  render={({ field }) => (
                    <FormItem className="col-span-2">
                      <FormLabel>Name</FormLabel>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="sort_order"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Sort order</FormLabel>
                      <FormControl>
                        <Input type="number" min={0} {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="space-y-4 pt-6">
                  <FormField
                    control={form.control}
                    name="is_default"
                    render={({ field }) => (
                      <FormItem className="flex items-center gap-2 space-y-0">
                        <FormControl>
                          <Checkbox
                            checked={field.value}
                            onCheckedChange={field.onChange}
                          />
                        </FormControl>
                        <FormLabel className="font-normal">
                          Default tier
                        </FormLabel>
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
                      </FormItem>
                    )}
                  />
                </div>
              </div>

              <div className="border-t pt-4">
                <div className="grid grid-cols-2 gap-4">
                  <FormField
                    control={form.control}
                    name="max_concurrent_workflows"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Max concurrent workflows</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            placeholder="Unlimited"
                            {...field}
                            value={field.value ?? ""}
                            onChange={(event) =>
                              field.onChange(
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value)
                              )
                            }
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="max_action_executions_per_workflow"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Max actions per workflow</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            placeholder="Unlimited"
                            {...field}
                            value={field.value ?? ""}
                            onChange={(event) =>
                              field.onChange(
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value)
                              )
                            }
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="max_concurrent_actions"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Max concurrent actions</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            placeholder="Unlimited"
                            {...field}
                            value={field.value ?? ""}
                            onChange={(event) =>
                              field.onChange(
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value)
                              )
                            }
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="api_rate_limit"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>API rate limit</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            placeholder="Unlimited"
                            {...field}
                            value={field.value ?? ""}
                            onChange={(event) =>
                              field.onChange(
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value)
                              )
                            }
                          />
                        </FormControl>
                        <FormDescription>Requests per second</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="api_burst_capacity"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>API burst capacity</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            placeholder="Unlimited"
                            {...field}
                            value={field.value ?? ""}
                            onChange={(event) =>
                              field.onChange(
                                event.target.value === ""
                                  ? null
                                  : Number(event.target.value)
                              )
                            }
                          />
                        </FormControl>
                        <FormDescription>
                          Maximum burst requests
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              </div>

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setDialogOpen(false)}
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
