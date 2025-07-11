import NoContent from "@/components/no-content"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

interface FlatKVTableProps<T> {
  keyName: keyof T
  valueName: keyof T
  data: T[]
}
function FlatKVTable<T>({ keyName, valueName, data }: FlatKVTableProps<T>) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="grid h-6 grid-cols-2 text-xs capitalize ">
          <TableHead className="col-span-1 font-bold">
            {keyName as string}
          </TableHead>
          <TableHead className="col-span-1 font-bold">
            {valueName as string}
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((pair, idx) => (
          <TableRow key={idx} className="grid grid-cols-2 text-xs">
            <TableCell className="col-span-1">
              {pair[keyName] as string}
            </TableCell>
            <TableCell className="col-span-1">
              {pair[valueName] as string}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

interface LabelsTableProps<T> {
  keyName: keyof T
  valueName: keyof T
  labels: T[]
  emptyMessage?: string
}

export function LabelsTable<T>({
  keyName,
  valueName,
  labels,
  emptyMessage = "No labels availbale",
}: LabelsTableProps<T>) {
  return labels.length > 0 ? (
    <FlatKVTable<T> keyName={keyName} valueName={valueName} data={labels} />
  ) : (
    <NoContent message={emptyMessage} />
  )
}
