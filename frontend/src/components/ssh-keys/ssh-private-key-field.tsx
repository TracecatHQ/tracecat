import type {
  Control,
  FieldPath,
  FieldValues,
  UseFormRegister,
} from "react-hook-form"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Textarea } from "@/components/ui/textarea"

interface SshPrivateKeyFieldProps<T extends FieldValues> {
  control: Control<T>
  register: UseFormRegister<T>
  name: FieldPath<T>
}

const sshPrivateKeyPlaceholder =
  "Starts with '-----BEGIN OPENSSH PRIVATE KEY-----"

export function SshPrivateKeyField<T extends FieldValues>({
  control,
  register,
  name,
}: SshPrivateKeyFieldProps<T>) {
  return (
    <FormField
      control={control}
      name={name}
      render={() => (
        <FormItem>
          <FormLabel className="text-sm">Key</FormLabel>
          <FormControl>
            <Textarea
              className="h-36 text-sm"
              placeholder={sshPrivateKeyPlaceholder}
              {...register(name)}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}
