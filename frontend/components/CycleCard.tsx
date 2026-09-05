import type { DecisionAuditRead } from "@/lib/types";
import { formatCurrency, formatDateTime, formatPercent } from "@/lib/format";
import { ActionBadge, ExecutionBadge, OutcomeBadge, PolicyBadge } from "@/components/Badge";
import { ProbabilityBar, EIRVBar } from "@/components/DecisionValueBars";

const ORDER = ["RETRY", "MESSAGE", "NO_ACTION"] as const;

export function CycleCard({ cycle, delayMs = 0 }: { cycle: DecisionAuditRead; delayMs?: number }) {
  const rows = ORDER.map((a) => cycle.actions_considered.find((c) => c.action === a)).filter(
    Boolean
  );
  const maxAbsEirv = Math.max(1, ...rows.map((r) => Math.abs(r!.eirv_value ?? 0)));

  return (
    <div
      className="animate-fade-up relative rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
      style={{ "--delay": `${delayMs}ms` } as React.CSSProperties}
    >
      <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-gray-900">
          Decision Cycle #{cycle.cycle_number}
        </h3>
        <span className="text-xs text-gray-400">{formatDateTime(cycle.decision_timestamp)}</span>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* AI Recovery Prediction */}
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-blue-600">
            AI Recovery Prediction
          </h4>
          <p className="mb-3 text-[11px] text-gray-400">Predicted recovery probability per action</p>
          <div className="space-y-2.5">
            {rows.map((row) => (
              <div key={row!.action}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <ActionBadge action={row!.action} />
                  <span className="font-medium text-gray-700">
                    {formatPercent(row!.recovery_probability)}
                  </span>
                </div>
                <ProbabilityBar action={row!.action} pct={row!.recovery_probability ?? 0} />
              </div>
            ))}
          </div>
          {cycle.model_version && (
            <p className="mt-2 text-[11px] text-gray-400">
              {cycle.model_version.model_name} · {cycle.model_version.version} ·{" "}
              {cycle.model_version.algorithm ?? "unknown algorithm"} · {cycle.model_version.status}
            </p>
          )}
        </div>

        {/* Decision Value (EIRV) */}
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-purple-600">
            Decision Value (EIRV)
          </h4>
          <p className="mb-3 text-[11px] text-gray-400">
            ML predicts probability. EIRV converts it into economic value.
          </p>
          <div className="space-y-2.5">
            {rows.map((row) => (
              <div key={row!.action}>
                <div className="mb-1 flex items-center justify-between text-xs">
                  <ActionBadge action={row!.action} />
                  <span className="font-medium text-gray-700">
                    {row!.eirv_value != null
                      ? `${row!.eirv_value >= 0 ? "+" : ""}${formatCurrency(row!.eirv_value)}`
                      : "-"}
                  </span>
                </div>
                <EIRVBar value={row!.eirv_value ?? 0} maxAbs={maxAbsEirv} />
              </div>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-gray-400">
            Cost used: {rows.map((r) => `${r!.action} ${r!.cost_used != null ? formatCurrency(r!.cost_used) : "-"}`).join(" · ")}
          </p>
        </div>
      </div>

      {/* AI Recommendation -> EIRV -> Policy Check -> Final Action */}
      <div className="mt-5 flex flex-wrap items-center gap-2 overflow-x-auto rounded-lg bg-gray-50 p-4 text-sm">
        <span className="text-xs font-medium text-gray-400">AI Recommendation</span>
        <ActionBadge action={cycle.recommended_action} />
        <Arrow />
        <span className="text-xs font-medium text-gray-400">Policy Check</span>
        <PolicyBadge result={cycle.was_blocked ? "BLOCKED" : "ALLOWED"} />
        <Arrow />
        <span className="text-xs font-medium text-gray-400">Final Action</span>
        <ActionBadge action={cycle.final_action} />
        {cycle.was_blocked && (
          <span className="ml-1 text-xs text-amber-700">— policy overrode the AI recommendation</span>
        )}
      </div>
      {cycle.decision_reason && <p className="mt-2 text-xs text-gray-400">{cycle.decision_reason}</p>}
      {cycle.was_blocked && cycle.block_reason_codes.length > 0 && (
        <p className="mt-1 text-xs text-amber-700">Blocked: {cycle.block_reason_codes.join(", ")}</p>
      )}

      {/* Intervention + Outcome */}
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-gray-100 p-4">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Execution
          </h4>
          {cycle.intervention_action ? (
            <div className="space-y-1 text-sm text-gray-700">
              <div className="flex items-center gap-2">
                <ActionBadge action={cycle.intervention_action} />
                {cycle.intervention_channel && (
                  <span className="text-gray-500">via {cycle.intervention_channel}</span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-gray-500">Execution status:</span>
                <ExecutionBadge status={cycle.execution_status} />
              </div>
              {cycle.intervention_cost != null && (
                <div className="text-gray-500">
                  Cost incurred: {formatCurrency(cycle.intervention_cost)}
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-400">No intervention — NO_ACTION.</p>
          )}
        </div>

        <div className="rounded-lg border border-gray-100 p-4">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Outcome
          </h4>
          {cycle.outcome_result ? (
            <div className="space-y-1 text-sm text-gray-700">
              <OutcomeBadge result={cycle.outcome_result} />
              {cycle.outcome_result === "RECOVERED" && cycle.outcome_recovery_amount != null && (
                <div className="text-gray-500">
                  Amount recovered: {formatCurrency(cycle.outcome_recovery_amount)}
                </div>
              )}
              <div className="text-gray-400">
                Observed at {formatDateTime(cycle.outcome_observed_at)}
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-400">Outcome pending — not yet observed.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function Arrow() {
  return (
    <span className="text-gray-300" aria-hidden>
      →
    </span>
  );
}
