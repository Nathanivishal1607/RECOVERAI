const COLORS = {
  neutral: "bg-gray-100 text-gray-700 ring-gray-200",
  blue: "bg-blue-50 text-blue-700 ring-blue-200",
  purple: "bg-purple-50 text-purple-700 ring-purple-200",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  red: "bg-red-50 text-red-700 ring-red-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
} as const;

export type BadgeColor = keyof typeof COLORS;

export function Badge({
  children,
  color = "neutral",
}: {
  children: React.ReactNode;
  color?: BadgeColor;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${COLORS[color]}`}
    >
      {children}
    </span>
  );
}

const CASE_STATUS_COLOR: Record<string, BadgeColor> = {
  OPEN: "blue",
  ANALYZING: "blue",
  ACTION_SELECTED: "blue",
  ACTION_EXECUTED: "blue",
  WAITING_FOR_OUTCOME: "amber",
  RECOVERED: "green",
  STOPPED: "neutral",
  EXPIRED: "neutral",
  FAILED: "red",
};

export function CaseStatusBadge({ status }: { status: string }) {
  return <Badge color={CASE_STATUS_COLOR[status] ?? "neutral"}>{status.replace(/_/g, " ")}</Badge>;
}

const ACTION_COLOR: Record<string, BadgeColor> = {
  RETRY: "blue",
  MESSAGE: "purple",
  NO_ACTION: "neutral",
};

export function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <Badge color="neutral">-</Badge>;
  return <Badge color={ACTION_COLOR[action] ?? "neutral"}>{action.replace(/_/g, " ")}</Badge>;
}

export function PolicyBadge({ result }: { result: string | null }) {
  if (!result) return <Badge color="neutral">not checked</Badge>;
  return <Badge color={result === "ALLOWED" ? "green" : "red"}>{result}</Badge>;
}

const EXEC_COLOR: Record<string, BadgeColor> = {
  REQUESTED: "neutral",
  ACCEPTED: "green",
  REJECTED: "amber",
  FAILED: "red",
};

export function ExecutionBadge({ status }: { status: string | null }) {
  if (!status) return <Badge color="neutral">-</Badge>;
  return <Badge color={EXEC_COLOR[status] ?? "neutral"}>{status}</Badge>;
}

export function OutcomeBadge({ result }: { result: string | null }) {
  if (!result) return <Badge color="neutral">pending</Badge>;
  return <Badge color={result === "RECOVERED" ? "green" : "red"}>{result.replace(/_/g, " ")}</Badge>;
}
