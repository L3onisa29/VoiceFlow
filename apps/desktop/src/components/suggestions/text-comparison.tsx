/** Before/after pair with the changed span marked as deleted and inserted. */
export function TextComparison({
  before,
  after,
  afterLabel = "Dopo",
}: {
  before: string;
  after: string;
  afterLabel?: string;
}) {
  let start = 0;
  while (
    start < before.length &&
    start < after.length &&
    before[start] === after[start]
  )
    start++;
  let end = 0;
  while (
    end < before.length - start &&
    end < after.length - start &&
    before[before.length - end - 1] === after[after.length - end - 1]
  )
    end++;
  const changed = before !== after;
  return (
    <dl className="grid gap-2 text-sm sm:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-1 rounded-lg bg-muted/50 p-3">
        <dt className="text-xs text-muted-foreground">Prima</dt>
        <dd className="break-words whitespace-pre-wrap">
          {changed ? (
            <>
              {before.slice(0, start)}
              <del className="text-destructive">
                {before.slice(start, before.length - end)}
              </del>
              {end > 0 ? before.slice(-end) : ""}
            </>
          ) : (
            before
          )}
        </dd>
      </div>
      <div className="flex min-w-0 flex-col gap-1 rounded-lg bg-muted/50 p-3">
        <dt className="text-xs text-muted-foreground">{afterLabel}</dt>
        <dd className="break-words whitespace-pre-wrap">
          {changed ? (
            <>
              {after.slice(0, start)}
              <ins className="text-success">
                {after.slice(start, after.length - end)}
              </ins>
              {end > 0 ? after.slice(-end) : ""}
              {after.length === 0 ? "Testo rimosso" : ""}
            </>
          ) : (
            after
          )}
        </dd>
      </div>
    </dl>
  );
}
