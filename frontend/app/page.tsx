import Link from "next/link";
import { getDashboard, getRecoveryCaseDetail, ApiError } from "@/lib/api";
import { formatCurrency } from "@/lib/format";
import { StatCard } from "@/components/StatCard";
import { BreakdownBar } from "@/components/BreakdownBar";
import { ApiErrorPanel } from "@/components/ApiErrorPanel";
import { DecisionPipelineDemo } from "@/components/DecisionPipelineDemo";
import { HowRecoverAIWorks } from "@/components/HowRecoverAIWorks";
import { RecoveryByActionCard } from "@/components/RecoveryByActionCard";
import { PolicyOverridesCard } from "@/components/PolicyOverridesCard";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let data;
  try {
    data = await getDashboard();
  } catch (err) {
    return <ApiErrorPanel error={err as ApiError} />;
  }

  const recoveryRate = data.total_cases > 0 ? data.recovered_cases / data.total_cases : 0;

  // The live pipeline demo below is built entirely from one real,
  // already-persisted case — never invented values.
  let heroCase = null;
  const heroId = data.highlighted_cases.hero_recovered_case_id;
  if (heroId) {
    try {
      heroCase = await getRecoveryCaseDetail(heroId);
    } catch {
      heroCase = null; // demo section just won't render — no fake fallback
    }
  }
  const heroCycle = heroCase?.cycles.find(
    (c) =>
      c.outcome_result === "RECOVERED" &&
      (c.final_action === "RETRY" || c.final_action === "MESSAGE")
  );

  return (
    <div className="space-y-10">
      <div className="animate-fade-up">
        <h1 className="text-3xl font-semibold tracking-tight text-gray-900">RecoverAI</h1>
        <p className="mt-1 text-sm font-medium text-gray-500">Revenue Recovery Intelligence</p>
        <p className="mt-2 max-w-2xl text-sm text-gray-500">
          RecoverAI doesn&apos;t just predict whether a payment will recover. It evaluates the
          economic value of each possible action, checks policy, takes the safest valuable
          action, observes the outcome, and learns from it.
        </p>
      </div>

      {/* KPI row */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="Revenue at Risk"
          value={formatCurrency(data.total_amount_at_risk)}
          numericValue={parseFloat(data.total_amount_at_risk)}
          format="currency"
          delayMs={0}
        />
        <StatCard
          label="Revenue Recovered"
          value={formatCurrency(data.total_recovery_amount)}
          numericValue={parseFloat(data.total_recovery_amount)}
          format="currency"
          accent="green"
          delayMs={40}
        />
        <StatCard
          label="Recovery Rate"
          value={`${(recoveryRate * 100).toFixed(1)}%`}
          numericValue={recoveryRate * 100}
          format="percent1"
          sub={`${data.recovered_cases.toLocaleString("en-IN")} of ${data.total_cases.toLocaleString("en-IN")} cases`}
          accent="green"
          delayMs={80}
        />
        <StatCard
          label="Recovery Cases"
          value={data.total_cases.toLocaleString("en-IN")}
          numericValue={data.total_cases}
          format="integer"
          sub={`${data.open_cases.toLocaleString("en-IN")} in progress`}
          accent="blue"
          delayMs={120}
        />
        <StatCard
          label="Policy Blocks"
          value={data.policy_blocked_count.toLocaleString("en-IN")}
          numericValue={data.policy_blocked_count}
          format="integer"
          sub="AI recommendations overridden"
          accent="amber"
          delayMs={160}
        />
      </section>

      {/* AI Decision Engine */}
      <section className="animate-fade-up rounded-xl border border-gray-200 bg-white p-6 shadow-sm" style={{ "--delay": "120ms" } as React.CSSProperties}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-gray-900">AI Decision Engine</h2>
            <p className="text-xs text-gray-500">
              Final actions authorized across every decision cycle RecoverAI has run.
            </p>
          </div>
          {heroCycle?.model_version && (
            <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-200">
              {heroCycle.model_version.model_name} · {heroCycle.model_version.algorithm} ·{" "}
              {heroCycle.model_version.status}
            </span>
          )}
        </div>
        <BreakdownBar
          rows={[
            { label: "RETRY", value: data.action_counts.RETRY },
            { label: "MESSAGE", value: data.action_counts.MESSAGE },
            { label: "NO_ACTION", value: data.action_counts.NO_ACTION },
          ]}
        />
      </section>

      {/* How RecoverAI decides — a live, real example */}
      {heroCycle && heroCase && (
        <section>
          <div className="mb-1 text-center">
            <h2 className="text-lg font-semibold text-gray-900">How RecoverAI Decides</h2>
            <p className="text-sm text-gray-500">
              A real recovery case, end to end — {heroCase.case_display_id}
            </p>
          </div>
          <div className="mt-6">
            <DecisionPipelineDemo
              cycle={heroCycle}
              caseDisplayId={heroCase.case_display_id}
              paymentDisplayId={heroCase.payment?.display_id ?? null}
              amount={heroCase.amount_at_risk}
              currency={heroCase.payment?.currency ?? "INR"}
            />
          </div>
          <div className="mt-4 text-center">
            <Link
              href={`/cases/${heroCase.recovery_case_id}`}
              className="text-sm font-medium text-blue-700 transition-colors hover:text-blue-900 hover:underline"
            >
              View the full decision record →
            </Link>
          </div>
        </section>
      )}

      <HowRecoverAIWorks />

      {/* Business impact — secondary to the decision-engine story */}
      <section>
        <h2 className="mb-4 text-sm font-semibold text-gray-900">Business Impact</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <StatCard
            label="Recovered"
            value={data.recovered_cases.toLocaleString("en-IN")}
            accent="green"
          />
          <StatCard
            label="Not Recovered"
            value={data.not_recovered_cases.toLocaleString("en-IN")}
            accent="red"
          />
          <StatCard
            label="Decision Cycles"
            value={data.decision_cycle_count.toLocaleString("en-IN")}
            sub="total evaluate -> decide cycles run"
          />
        </div>
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <RecoveryByActionCard data={data.recovery_by_action} />
          <PolicyOverridesCard
            count={data.policy_blocked_count}
            policyBlockCaseId={data.highlighted_cases.policy_block_case_id}
          />
        </div>
      </section>

      <div>
        <Link
          href="/cases"
          className="inline-flex items-center rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-gray-800 active:scale-95"
        >
          View all recovery cases →
        </Link>
      </div>
    </div>
  );
}
