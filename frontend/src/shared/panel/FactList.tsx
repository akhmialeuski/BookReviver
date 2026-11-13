/**
 * The list of facts a panel states about the open page or file: a label on the left and its value on the right, one row
 * for each fact.
 *
 * It is the content of the `facts` of the page slot on every stage, so a fact looks the same whether a step found it on a
 * page or the file carries it.
 */

export function FactList({
  facts,
  ...props
}: {
  facts: readonly { label: string; value: string }[];
} & React.ComponentProps<'dl'>): React.JSX.Element {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" {...props}>
      {facts.map((fact) => (
        <div key={fact.label} className="col-span-2 flex justify-between gap-4">
          <dt className="text-muted-foreground">{fact.label}</dt>
          <dd className="text-right font-medium">{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}
