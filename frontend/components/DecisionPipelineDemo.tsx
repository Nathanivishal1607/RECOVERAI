import type { DecisionAuditRead } from "@/lib/types";
import { formatCurrency, formatPercent } from "@/lib/format";
import { ActionBadge, OutcomeBadge, PolicyBadge } from "@/components/Badge";
import { ProbabilityBar, EIRVBar } from "@/components/DecisionValueBars";
import { PipelineNode } from "@/components/PipelineNode";

const ORDER = ["RETRY", "MESSAGE", "NO_ACTION"] as const;

/**
 * The dashboard's live "How RecoverAI Decides" walkthrough — built
 * entirely from one real decision cycle (`GET /api/recovery-cases/{id}`),
 * never invented values. Mirrors the actual pipeline: T-Learner produces
 * probabilities; the value engine (not the model) derives EIRV; policy
 * evaluates the recommendation; the final action is what was actually
 * authorized and executed.
 */
export function DecisionPipelineDemo({
  cycle,
  caseDisplayId,
  paymentDisplayId,
  amount,
  currency,
}: {
  cycle: DecisionAuditRead;
  caseDisplayId: string;
  paymentDisplayId: string | null;
  amount: string;
  currency: string;
}) {
  const rows = ORDER.map((a) => cycle.actions_considered.find((c) => c.action === a)).filter(
    Boolean
  );
  const maxAbsEirv = Math.max(1, ...rows.map((r) => Math.abs(r!.eirv_value ?? 0)));

  return (
    <div className="flex flex-col items-center">
      <PipelineNode label="Payment Failed" accent="gray" delayMs={0}>
        <div className="text-sm font-medium text-gray-800">
          {paymentDisplayId ?? "Payment"} · {formatCurrency(amount, currency)}
        </div>
        <div className="mt-1 text-xs text-gray-400">Recovery case {caseDisplayId}</div>
      </PipelineNode>

      <PipelineNode label="AI Prediction (T-Learner)" accent="blue" delayMs={140}>
        <div className="space-y-2.5">
          {rows.map((r, i) => (
            <div key={r!.action}>
              <div className="mb-1 flex items-center justify-between text-xs">
                <ActionBadge action={r!.action} />
                <span className="font-medium text-gray-700">
                  {formatPercent(r!.recovery_probability)}
                </span>
              </div>
              <ProbabilityBar
                action={r!.action}
                pct={r!.recovery_probability ?? 0}
                delayMs={200 + i * 80}
              />
            </div>
          ))}
        </div>
        {cycle.model_version && (
          <p className="mt-2 text-[11px] text-gray-400">
            {cycle.model_version.model_name} · {cycle.model_version.algorithm} ·{" "}
            {cycle.model_version.status}
          </p>
        )}
      </PipelineNode>

      <PipelineNode label="EIRV — Decision Value Engine" accent="purple" delayMs={300}>
        <div className="space-y-2.5">
          {rows.map((r, i) => (
            <div key={r!.action}>
              <div className="mb-1 flex items-center justify-between text-xs">
                <ActionBadge action={r!.action} />
                <span className="font-medium text-gray-700">
                  {r!.eirv_value != null
                    ? `${r!.eirv_value >= 0 ? "+" : ""}${formatCurrency(r!.eirv_value)}`
                    : "-"}
                </span>
              </div>
              <EIRVBar value={r!.eirv_value ?? 0} maxAbs={maxAbsEirv} delayMs={360 + i * 80} />
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-gray-400">
          Computed by the decision engine — probability × amount − cost. The model never
          computes this.
        </p>
      </PipelineNode>

      <PipelineNode
        label="Policy Check"
        accent={cycle.was_blocked ? "amber" : "green"}
        delayMs={460}
      >
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Recommended</span>
          <ActionBadge action={cycle.recommended_action} />
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          <span className="text-xs text-gray-500">Policy</span>
          <PolicyBadge result={cycle.was_blocked ? "BLOCKED" : "ALLOWED"} />
        </div>
      </PipelineNode>

      <PipelineNode label="Final Action" accent="green" delayMs={560}>
        <ActionBadge action={cycle.final_action} />
        {cycle.was_blocked && (
          <p className="mt-1 text-[11px] text-gray-400">Overridden by policy from AI recommendation</p>
        )}
      </PipelineNode>

      <PipelineNode
        label="Outcome"
        accent={cycle.outcome_result === "RECOVERED" ? "green" : "red"}
        delayMs={640}
        isLast
      >
        <OutcomeBadge result={cycle.outcome_result} />
        {cycle.outcome_result === "RECOVERED" && cycle.outcome_recovery_amount != null && (
          <div className="mt-1 text-sm font-medium text-gray-800">
            {formatCurrency(cycle.outcome_recovery_amount)}
          </div>
        )}
      </PipelineNode>
    </div>
  );
}
