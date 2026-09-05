const ACTION_BAR_COLOR: Record<string, string> = {
  RETRY: "bg-blue-500",
  MESSAGE: "bg-purple-500",
  NO_ACTION: "bg-gray-400",
};

/** A single 0-100% probability bar (AI prediction — blue-family). */
export function ProbabilityBar({
  action,
  pct,
  delayMs = 0,
}: {
  action: string;
  pct: number; // 0..1
  delayMs?: number;
}) {
  const width = `${Math.max(0, Math.min(1, pct)) * 100}%`;
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
      <div
        className={`animate-grow-bar h-full rounded-full ${ACTION_BAR_COLOR[action] ?? "bg-blue-500"}`}
        style={{ width, "--target": width, "--delay": `${delayMs}ms` } as React.CSSProperties}
      />
    </div>
  );
}

/**
 * A diverging value bar for EIRV (can be negative): grows right from
 * center for positive value, left from center for negative — never
 * misrepresents a loss as a small positive bar.
 */
export function EIRVBar({
  value,
  maxAbs,
  delayMs = 0,
}: {
  value: number;
  maxAbs: number;
  delayMs?: number;
}) {
  const pct = maxAbs > 0 ? Math.min(1, Math.abs(value) / maxAbs) * 50 : 0;
  const width = `${pct}%`;
  const positive = value >= 0;
  return (
    <div className="relative h-2 w-full overflow-hidden rounded-full bg-gray-100">
      <div className="absolute left-1/2 top-0 h-full w-px bg-gray-300" />
      {positive ? (
        <div
          className="animate-grow-bar absolute left-1/2 top-0 h-full rounded-r-full bg-purple-500"
          style={{ width, "--target": width, "--delay": `${delayMs}ms` } as React.CSSProperties}
        />
      ) : (
        <div
          className="animate-grow-bar absolute right-1/2 top-0 h-full rounded-l-full bg-gray-400"
          style={{ width, "--target": width, "--delay": `${delayMs}ms` } as React.CSSProperties}
        />
      )}
    </div>
  );
}
