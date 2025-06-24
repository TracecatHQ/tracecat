"use client"

import type { UserRead } from "@/client"

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

function displayValue(value: unknown): string {
  console.log("displayValue", value)
  if (value === null || value === undefined) {
    return "-"
  } else if (typeof value === "object") {
    return (
      Object.entries(value as Record<string, unknown>)
        .map(
          ([key, value]) => `${key}=${value ? JSON.stringify(value) : "null"}`
        )
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
