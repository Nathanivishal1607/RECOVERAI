import Link from "next/link";

export function PolicyOverridesCard({
  count,
  policyBlockCaseId,
}: {
  count: number;
  policyBlockCaseId: string | null;
}) {
  const body = (
    <div className="animate-fade-up rounded-xl border border-amber-200 bg-amber-50 p-6 shadow-sm transition-colors hover:bg-amber-100/70">
      <h2 className="text-sm font-semibold text-amber-900">Policy Overrides</h2>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-amber-900">
        {count.toLocaleString("en-IN")}
      </p>
      <p className="mt-1 text-xs text-amber-800">
        {count === 1 ? "decision" : "decisions"} where policy overrode the AI recommendation —
        proof the AI cannot authorize an action on its own.
      </p>
      {policyBlockCaseId && (
        <p className="mt-3 text-xs font-medium text-amber-900 underline underline-offset-2">
          View a real example →
        </p>
      )}
    </div>
  );

  if (!policyBlockCaseId) return body;
  return (
    <Link href={`/cases/${policyBlockCaseId}`} className="block">
      {body}
    </Link>
  );
}
