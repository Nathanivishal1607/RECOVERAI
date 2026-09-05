const STEPS: { label: string; blurb: string; accent: string }[] = [
  { label: "Payment Failure", blurb: "A payment fails and enters recovery eligibility.", accent: "border-gray-300 text-gray-500" },
  { label: "Feature Snapshot", blurb: "Decision-time signals are frozen for this cycle — no future data.", accent: "border-gray-300 text-gray-500" },
  { label: "T-Learner", blurb: "Estimates recovery probability under each candidate action.", accent: "border-blue-300 text-blue-600" },
  { label: "Recovery Probability", blurb: "P(recover) for RETRY, MESSAGE, and NO_ACTION.", accent: "border-blue-300 text-blue-600" },
  { label: "EIRV Decision Engine", blurb: "Converts probability into expected economic value — not the model's job.", accent: "border-purple-300 text-purple-600" },
  { label: "Policy Engine", blurb: "Applies business/safety constraints before anything executes.", accent: "border-amber-300 text-amber-700" },
  { label: "Final Action", blurb: "The authorized action — may differ from the AI recommendation.", accent: "border-emerald-300 text-emerald-600" },
  { label: "Outcome", blurb: "Measures what actually happened to the payment.", accent: "border-emerald-300 text-emerald-600" },
  { label: "Learning Data", blurb: "The observed decision becomes a TrainingExample for future models.", accent: "border-gray-300 text-gray-500" },
];

export function HowRecoverAIWorks() {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-gray-900">How RecoverAI Works</h2>
      <p className="mt-1 text-xs text-gray-500">
        The general architecture behind every decision — see a live example above.
      </p>
      <div className="mt-5 flex flex-wrap items-stretch gap-x-1 gap-y-4">
        {STEPS.map((step, i) => (
          <div key={step.label} className="flex items-stretch">
            <div
              className={`animate-fade-up flex w-40 flex-col rounded-lg border ${step.accent} bg-white p-3`}
              style={{ "--delay": `${i * 60}ms` } as React.CSSProperties}
            >
              <span className="text-xs font-bold uppercase tracking-wide">{step.label}</span>
              <span className="mt-1 text-[11px] leading-snug text-gray-500">{step.blurb}</span>
            </div>
            {i < STEPS.length - 1 && (
              <span className="mx-1.5 hidden self-center text-gray-300 sm:inline" aria-hidden>
                →
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
