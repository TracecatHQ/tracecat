import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export function ShallowKVTable({
  keyName,
  valueName,
  data,
}: {
  keyName: string
  valueName: string
  data: Record<string, string>
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="grid h-6 grid-cols-2 text-xs">
          <TableHead className="col-span-1">{keyName}</TableHead>
          <TableHead className="col-span-1">{valueName}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {Object.entries(data).map(([key, value], idx) => (
          <TableRow key={idx} className="grid grid-cols-2 text-xs">
            <TableCell className="col-span-1">{key}</TableCell>
            <TableCell className="col-span-1">{value}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
