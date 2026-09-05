const BAR_COLOR: Record<string, string> = {
  RETRY: "bg-blue-500",
  MESSAGE: "bg-purple-500",
  NO_ACTION: "bg-gray-400",
  ACCEPTED: "bg-emerald-500",
  REJECTED: "bg-amber-500",
  FAILED: "bg-red-500",
  REQUESTED: "bg-gray-400",
};

export function BreakdownBar({
  rows,
}: {
  rows: { label: string; value: number }[];
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <div key={row.label}>
          <div className="mb-1 flex items-center justify-between text-sm">
            <span className="font-medium text-gray-700">{row.label.replace(/_/g, " ")}</span>
            <span className="text-gray-500">{row.value.toLocaleString("en-IN")}</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
            <div
              className={`h-full rounded-full ${BAR_COLOR[row.label] ?? "bg-gray-500"}`}
              style={{ width: `${(row.value / max) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
