import { NamedPair } from "@/types/generics"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import NoContent from "@/components/no-content"

interface FlatKVTableProps<KName extends string, VName extends string, VType> {
  keyName: KName
  valueName: VName
  data: NamedPair<KName, VName, VType>[]
}
function FlatKVTable<KeyName extends string, ValueName extends string, TValue>({
  keyName,
  valueName,
  data,
}: FlatKVTableProps<KeyName, ValueName, TValue>) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="grid h-6 grid-cols-2 text-xs capitalize ">
          <TableHead className="col-span-1 font-bold">{keyName}</TableHead>
          <TableHead className="col-span-1 font-bold">{valueName}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((pair, idx) => (
          <TableRow key={idx} className="grid grid-cols-2 text-xs">
            <TableCell className="col-span-1">
              {pair[keyName as keyof typeof pair] as string}
            </TableCell>
            <TableCell className="col-span-1">
              {pair[valueName as keyof typeof pair] as string}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

interface LabelsTableProps<KName extends string, VName extends string, VType> {
  keyName: KName
  valueName: VName
  labels: NamedPair<KName, VName, VType>[] | null
  emptyMessage?: string
}

export function LabelsTable<KName extends string, VName extends string, VType>({
  keyName,
  valueName,
  labels,
  emptyMessage = "No labels availbale",
}: LabelsTableProps<KName, VName, VType>) {
  return labels ? (
    <FlatKVTable<KName, VName, VType>
      keyName={keyName}
      valueName={valueName}
      data={labels}
    />
  ) : (
    <NoContent message={emptyMessage} />
  )
}
