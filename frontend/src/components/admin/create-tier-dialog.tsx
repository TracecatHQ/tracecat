"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { PlusIcon } from "lucide-react"
import { useState } from "react"
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
import { useAdminTiers } from "@/hooks/use-admin"

// Preprocessor to convert empty strings to undefined for optional numeric fields
const emptyToUndefined = z.preprocess(
  (val) => (val === "" ? undefined : val),
  z.coerce.number().int().positive().optional()
)

const formSchema = z.object({
  display_name: z
    .string()
    .min(1, "Name is required")
    .max(50, "Name is too long"),
  max_concurrent_workflows: emptyToUndefined,
  max_action_executions_per_workflow: emptyToUndefined,
  max_concurrent_actions: emptyToUndefined,
  api_rate_limit: emptyToUndefined,
  api_burst_capacity: emptyToUndefined,
  is_default: z.boolean().default(false),
  sort_order: z.coerce.number().int().min(0).default(0),
})

type FormValues = z.infer<typeof formSchema>

export function CreateTierDialog() {
  const [open, setOpen] = useState(false)
  const { createTier, createPending } = useAdminTiers()

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      display_name: "",
      is_default: false,
      sort_order: 0,
    },
  })

  const onSubmit = async (values: FormValues) => {
    try {
      await createTier({
        display_name: values.display_name,
        max_concurrent_workflows: values.max_concurrent_workflows ?? null,
        max_action_executions_per_workflow:
          values.max_action_executions_per_workflow ?? null,
        max_concurrent_actions: values.max_concurrent_actions ?? null,
        api_rate_limit: values.api_rate_limit ?? null,
        api_burst_capacity: values.api_burst_capacity ?? null,
        is_default: values.is_default,
        sort_order: values.sort_order,
      })
      toast({
        title: "Tier created",
        description: `${values.display_name} has been created successfully.`,
      })
      form.reset()
      setOpen(false)
    } catch (error) {
      console.error("Failed to create tier", error)
      toast({
        title: "Failed to create tier",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <PlusIcon className="mr-2 size-4" />
          New tier
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Create tier</DialogTitle>
          <DialogDescription>
            Create a new tier with resource limits and entitlements.
          </DialogDescription>
        </DialogHeader>
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
                      <Input placeholder="Pro" {...field} />
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
              <FormField
                control={form.control}
                name="is_default"
                render={({ field }) => (
                  <FormItem className="flex items-center gap-2 space-y-0 pt-6">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <FormLabel className="font-normal">Default tier</FormLabel>
                  </FormItem>
                )}
              />
            </div>

            <div className="border-t pt-4">
              <h4 className="text-sm font-medium mb-3">Resource limits</h4>
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
                        />
                      </FormControl>
                      <FormDescription>Requests per second</FormDescription>
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
                onClick={() => setOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={createPending}>
                {createPending ? "Creating..." : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
