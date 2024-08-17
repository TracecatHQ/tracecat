"use client"

import { UserRead } from "@/client"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

function displayValue(value: unknown): string {
  if (typeof value === "object") {
    return (
      Object.entries(value as Record<string, unknown>)
        .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
        .join(" ") || "-"
    )
  }
  return value?.toString() || "-"
}

export function UserDetails({ user }: { user: UserRead }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="font-bold">Key</TableHead>
          <TableHead className="font-bold">Value</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {Object.entries(user).map(([key, value]) => (
          <TableRow key={key}>
            <TableCell>{key}</TableCell>
            <TableCell>{displayValue(value)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
