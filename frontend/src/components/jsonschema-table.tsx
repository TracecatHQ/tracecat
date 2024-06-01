import { JSONSchema7 } from "json-schema"

import { transformJsonSchemaToTableRows } from "@/lib/jsonschema"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export function JSONSchemaTable({ schema }: { schema: JSONSchema7 }) {
  const rows = transformJsonSchemaToTableRows(schema)
  return (
    <Table>
      <TableHeader>
        <TableRow className="grid h-6 grid-cols-5 text-xs capitalize ">
          <TableHead className="col-span-1 font-bold">Parmaeter</TableHead>
          <TableHead className="col-span-1 font-bold">Type</TableHead>
          <TableHead className="col-span-1 font-bold">Default</TableHead>
          <TableHead className="col-span-1 font-bold">Description</TableHead>
          <TableHead className="col-span-1 font-bold">Constraints</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, idx) => (
          <TableRow
            key={idx}
            className="grid grid-cols-5 text-xs text-muted-foreground"
          >
            <TableCell className="col-span-1">{row.parameter}</TableCell>
            <TableCell className="col-span-1">{row.type}</TableCell>
            <TableCell className="col-span-1">{row.default}</TableCell>
            <TableCell className="col-span-1">{row.description}</TableCell>
            <TableCell className="col-span-1">{row.constraints}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
