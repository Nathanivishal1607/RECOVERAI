import { CountUp, type CountUpFormat } from "./CountUp";

const ACCENTS = {
  neutral: "border-gray-200",
  blue: "border-blue-300",
  green: "border-emerald-300",
  red: "border-red-300",
  amber: "border-amber-300",
  purple: "border-purple-300",
} as const;

export function StatCard({
  label,
  value,
  numericValue,
  format,
  sub,
  accent = "neutral",
  delayMs = 0,
}: {
  label: string;
  value: string;
  /** When provided with `format`, animates the KPI from 0 -> value. */
  numericValue?: number;
  format?: CountUpFormat;
  sub?: string;
  accent?: keyof typeof ACCENTS;
  delayMs?: number;
}) {
  return (
    <div
      className={`animate-fade-up rounded-xl border-t-4 ${ACCENTS[accent]} border-x border-b border-gray-200 bg-white p-5 shadow-sm`}
      style={{ "--delay": `${delayMs}ms` } as React.CSSProperties}
    >
      <div className="text-sm font-medium text-gray-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight text-gray-900">
        {numericValue !== undefined && format ? (
          <CountUp value={numericValue} format={format} />
        ) : (
          value
        )}
      </div>
      {sub && <div className="mt-1 text-xs text-gray-400">{sub}</div>}
    </div>
  );
}
