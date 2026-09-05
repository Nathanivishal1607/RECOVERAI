import Link from "next/link";
import { listRecoveryCases, ApiError } from "@/lib/api";
import { formatCurrency, formatDateTime } from "@/lib/format";
import { ActionBadge, CaseStatusBadge, OutcomeBadge } from "@/components/Badge";
import { ApiErrorPanel } from "@/components/ApiErrorPanel";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 25;
const STATUS_FILTERS = [
  "OPEN", "ANALYZING", "WAITING_FOR_OUTCOME", "RECOVERED", "STOPPED", "EXPIRED",
];

export default async function RecoveryCasesPage({
  searchParams,
}: {
  searchParams: { offset?: string; status?: string };
}) {
  const offset = Number(searchParams.offset ?? 0) || 0;
  const status = searchParams.status;

  let data;
  try {
    data = await listRecoveryCases({ limit: PAGE_SIZE, offset, status });
  } catch (err) {
    return <ApiErrorPanel error={err as ApiError} />;
  }

  const hasPrev = offset > 0;
  const hasNext = offset + data.items.length < data.total;
  const qs = (o: number) => {
    const p = new URLSearchParams();
    p.set("offset", String(o));
    if (status) p.set("status", status);
    return `/cases?${p.toString()}`;
  };

  return (
    <div className="space-y-6">
      <div className="animate-fade-up">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900">Recovery Cases</h1>
        <p className="mt-1 text-sm text-gray-500">
          {data.total.toLocaleString("en-IN")} case{data.total === 1 ? "" : "s"} total. Click a
          row for the full decision timeline.
        </p>
      </div>

      <div className="animate-fade-up flex flex-wrap gap-2" style={{ "--delay": "40ms" } as React.CSSProperties}>
        <Link
          href="/cases"
          className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition-colors ${
            !status ? "bg-gray-900 text-white ring-gray-900" : "bg-white text-gray-600 ring-gray-200 hover:bg-gray-50"
          }`}
        >
          All
        </Link>
        {STATUS_FILTERS.map((s) => (
          <Link
            key={s}
            href={`/cases?status=${s}`}
            className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition-colors ${
              status === s ? "bg-gray-900 text-white ring-gray-900" : "bg-white text-gray-600 ring-gray-200 hover:bg-gray-50"
            }`}
          >
            {s.replace(/_/g, " ")}
          </Link>
        ))}
      </div>

      <div className="animate-fade-up overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm" style={{ "--delay": "80ms" } as React.CSSProperties}>
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr className="text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              <th className="px-4 py-3">Case</th>
              <th className="px-4 py-3">Payment</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-blue-600">AI Recommendation</th>
              <th className="px-4 py-3">Final Action</th>
              <th className="px-4 py-3">Outcome</th>
              <th className="px-4 py-3">Opened</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {data.items.map((item) => (
              <tr key={item.recovery_case_id} className="transition-colors hover:bg-gray-50">
                <td className="px-4 py-3">
                  <Link
                    href={`/cases/${item.recovery_case_id}`}
                    className="font-medium text-blue-700 hover:underline"
                  >
                    {item.case_display_id}
                  </Link>
                  <div className="text-xs text-gray-400">{item.cycle_count} cycle{item.cycle_count === 1 ? "" : "s"}</div>
                </td>
                <td className="px-4 py-3 text-gray-600">{item.payment_display_id ?? "-"}</td>
                <td className="px-4 py-3 font-semibold text-gray-900">
                  {formatCurrency(item.payment_amount, item.currency)}
                </td>
                <td className="px-4 py-3">
                  <CaseStatusBadge status={item.status} />
                </td>
                <td className="px-4 py-3">
                  <ActionBadge action={item.latest_recommended_action} />
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1.5">
                    <ActionBadge action={item.latest_final_action} />
                    {item.latest_recommended_action &&
                      item.latest_final_action &&
                      item.latest_recommended_action !== item.latest_final_action && (
                        <span title="Overridden by policy" className="text-amber-500">
                          ⚠
                        </span>
                      )}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <OutcomeBadge result={item.latest_outcome_result} />
                </td>
                <td className="px-4 py-3 whitespace-nowrap text-gray-500">
                  {formatDateTime(item.opened_at)}
                </td>
              </tr>
            ))}
            {data.items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-400">
                  No recovery cases match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-500">
          Showing {data.items.length === 0 ? 0 : offset + 1}
          {"–"}
          {offset + data.items.length} of {data.total.toLocaleString("en-IN")}
        </span>
        <div className="flex gap-2">
          <Link
            href={qs(Math.max(0, offset - PAGE_SIZE))}
            aria-disabled={!hasPrev}
            className={`rounded-lg border px-3 py-1.5 transition-all active:scale-95 ${
              hasPrev ? "border-gray-300 text-gray-700 hover:bg-gray-50" : "pointer-events-none border-gray-200 text-gray-300"
            }`}
          >
            Previous
          </Link>
          <Link
            href={qs(offset + PAGE_SIZE)}
            aria-disabled={!hasNext}
            className={`rounded-lg border px-3 py-1.5 ${
              hasNext ? "border-gray-300 text-gray-700 hover:bg-gray-50" : "pointer-events-none border-gray-200 text-gray-300"
            }`}
          >
            Next
          </Link>
        </div>
      </div>
    </div>
  );
}
