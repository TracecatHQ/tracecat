import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2 } from "lucide-react"
import { useEffect } from "react"
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
import { Textarea } from "@/components/ui/textarea"
import { skillNameSchema } from "@/lib/skills-studio"
import { cn } from "@/lib/utils"

const createSkillDialogSchema = z.object({
  name: skillNameSchema,
  description: z.string().trim(),
})

export type CreateSkillDialogValues = z.infer<typeof createSkillDialogSchema>

type CreateSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  pending: boolean
  onCreate: (values: CreateSkillDialogValues) => Promise<void>
}

function normalizeSkillNameInput(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/_/g, "-")
    .replace(/[^a-z0-9-\s]/g, "")
    .replace(/[\s-]+/g, "-")
}

export function CreateSkillDialog({
  open,
  onOpenChange,
  pending,
  onCreate,
}: CreateSkillDialogProps) {
  const form = useForm<CreateSkillDialogValues>({
    resolver: zodResolver(createSkillDialogSchema),
    defaultValues: {
      name: "",
      description: "",
    },
    mode: "onSubmit",
    reValidateMode: "onSubmit",
  })

  useEffect(() => {
    if (!open) {
      form.reset()
    }
  }, [form, open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create skill</DialogTitle>
          <DialogDescription>
            Start with an empty working copy seeded with a root SKILL.md file.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            className="flex flex-col gap-4"
            onSubmit={form.handleSubmit((values) => {
              if (pending) {
                return
              }
              return onCreate(values)
            })}
          >
            <div className="flex flex-col gap-3">
              <FormField
                control={form.control}
                name="name"
                render={({ field, fieldState }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        onChange={(event) => {
                          form.clearErrors("name")
                          field.onChange(
                            normalizeSkillNameInput(event.target.value)
                          )
                        }}
                        placeholder="threat-intel"
                        className={cn(
                          fieldState.invalid &&
                            "border-destructive focus-visible:ring-destructive"
                        )}
                      />
                    </FormControl>
                    <FormDescription>
                      Lowercase letters, numbers, and single hyphens. Max 64
                      characters.
                    </FormDescription>
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
                        {...field}
                        placeholder="Optional description"
                        rows={3}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={pending}>
                {pending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : null}
                Create skill
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
