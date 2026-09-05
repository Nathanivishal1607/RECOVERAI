import type { ReactNode } from "react";

const ACCENT = {
  gray: { border: "border-gray-300", text: "text-gray-500", ring: "bg-gray-100" },
  blue: { border: "border-blue-300", text: "text-blue-600", ring: "bg-blue-50" },
  purple: { border: "border-purple-300", text: "text-purple-600", ring: "bg-purple-50" },
  amber: { border: "border-amber-300", text: "text-amber-700", ring: "bg-amber-50" },
  green: { border: "border-emerald-300", text: "text-emerald-600", ring: "bg-emerald-50" },
  red: { border: "border-red-300", text: "text-red-600", ring: "bg-red-50" },
} as const;

export type PipelineAccent = keyof typeof ACCENT;

export function PipelineNode({
  label,
  accent = "gray",
  children,
  delayMs = 0,
  isLast = false,
}: {
  label: string;
  accent?: PipelineAccent;
  children: ReactNode;
  delayMs?: number;
  isLast?: boolean;
}) {
  const a = ACCENT[accent];
  return (
    <div className="flex flex-col items-center">
      <div
        className={`animate-node-in w-full max-w-sm rounded-xl border-2 ${a.border} bg-white p-4 shadow-sm`}
        style={{ "--delay": `${delayMs}ms` } as React.CSSProperties}
      >
        <div
          className={`mb-2 inline-block rounded px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${a.text} ${a.ring}`}
        >
          {label}
        </div>
        {children}
      </div>
      {!isLast && (
        <div
          className="animate-node-in my-1 text-lg leading-none text-gray-300"
          style={{ "--delay": `${delayMs + 60}ms` } as React.CSSProperties}
          aria-hidden
        >
          ↓
        </div>
      )}
    </div>
  );
}
