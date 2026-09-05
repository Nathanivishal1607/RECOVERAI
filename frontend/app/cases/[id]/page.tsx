import Link from "next/link";
import { notFound } from "next/navigation";
import { getRecoveryCaseDetail, ApiError } from "@/lib/api";
import { formatCurrency, formatDateTime } from "@/lib/format";
import { CaseStatusBadge } from "@/components/Badge";
import { CycleCard } from "@/components/CycleCard";
import { ApiErrorPanel } from "@/components/ApiErrorPanel";
import { ExplanationPanel } from "@/components/ExplanationPanel";

export const dynamic = "force-dynamic";

export default async function CaseDetailPage({ params }: { params: { id: string } }) {
  let data;
  try {
    data = await getRecoveryCaseDetail(params.id);
  } catch (err) {
    const apiErr = err as ApiError;
    if (apiErr.status === 404) notFound();
    return <ApiErrorPanel error={apiErr} />;
  }

  return (
    <div className="space-y-6">
      <div className="animate-fade-up">
        <Link href="/cases" className="text-sm text-gray-500 transition-colors hover:text-gray-700">
          ← All recovery cases
        </Link>
        <div className="mt-2 flex flex-wrap items-baseline gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-gray-900">
            {data.case_display_id}
          </h1>
          {data.payment && (
            <span className="text-sm text-gray-400">Payment {data.payment.display_id}</span>
          )}
          <span className="text-xl font-semibold text-gray-800">
            {formatCurrency(data.amount_at_risk, data.payment?.currency ?? "INR")}
          </span>
          <CaseStatusBadge status={data.status} />
        </div>
        <p className="mt-1 text-sm text-gray-500">
          Opened {formatDateTime(data.opened_at)}
          {data.closed_at && <> · Closed {formatDateTime(data.closed_at)}</>}
          {data.failure_category && <> · Failure: {data.failure_category}</>}
        </p>
      </div>

      {/* Payment + experiment context */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="animate-fade-up rounded-xl border border-gray-200 bg-white p-5 shadow-sm" style={{ "--delay": "60ms" } as React.CSSProperties}>
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Payment
          </h2>
          {data.payment ? (
            <dl className="space-y-1.5 text-sm">
              <Row label="Payment ID" value={data.payment.display_id} />
              <Row
                label="Amount at risk"
                value={formatCurrency(data.amount_at_risk, data.payment.currency)}
              />
              <Row label="Method" value={data.payment.payment_method ?? "-"} />
              <Row label="Payment status" value={data.payment.status} />
            </dl>
          ) : (
            <p className="text-sm text-gray-400">Payment record unavailable.</p>
          )}
          {data.payment_events.length > 0 && (
            <div className="mt-4 border-t border-gray-100 pt-3">
              <h3 className="mb-2 text-xs font-medium text-gray-400">Payment events</h3>
              <ul className="space-y-1 text-xs text-gray-500">
                {data.payment_events.map((e) => (
                  <li key={e.id} className="flex justify-between gap-2">
                    <span>{e.event_type.replace(/_/g, " ")}</span>
                    <span className="text-gray-400">{formatDateTime(e.event_timestamp)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="animate-fade-up rounded-xl border border-gray-200 bg-white p-5 shadow-sm" style={{ "--delay": "100ms" } as React.CSSProperties}>
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Experiment Assignment
          </h2>
          {data.experiment_assignment ? (
            <dl className="space-y-1.5 text-sm">
              <Row label="Experiment" value={data.experiment_assignment.experiment_name ?? "-"} />
              <Row label="Arm" value={data.experiment_assignment.arm} />
              <Row
                label="Assigned at"
                value={formatDateTime(data.experiment_assignment.assigned_at)}
              />
            </dl>
          ) : (
            <p className="text-sm text-gray-400">This case is not part of an experiment.</p>
          )}
        </div>
      </div>

      {/* Decision timeline — the auditable sequence of immutable cycles */}
      <div>
        <h2 className="mb-1 text-sm font-semibold text-gray-900">Decision Timeline</h2>
        <p className="mb-4 text-xs text-gray-500">
          {data.cycles.length} decision cycle{data.cycles.length === 1 ? "" : "s"} — each one
          immutable once decided.
        </p>
        <div className="space-y-0">
          {data.cycles.map((cycle, i) => (
            <div key={cycle.decision_record_id}>
              {i > 0 && (
                <div
                  className="animate-fade-up my-3 flex items-center justify-center gap-2 text-xs font-medium text-gray-400"
                  style={{ "--delay": `${i * 120}ms` } as React.CSSProperties}
                >
                  <span aria-hidden>↓</span> Re-evaluation
                </div>
              )}
              <CycleCard cycle={cycle} delayMs={i * 120} />
            </div>
          ))}
          {data.cycles.length === 0 && (
            <p className="text-sm text-gray-400">No decision cycles recorded yet.</p>
          )}
        </div>
      </div>

      {data.cycles.length > 0 && <ExplanationPanel caseId={data.recovery_case_id} />}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-gray-500">{label}</dt>
      <dd className="font-medium text-gray-800">{value}</dd>
    </div>
  );
}
