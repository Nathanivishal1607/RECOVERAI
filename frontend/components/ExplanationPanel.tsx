"use client";

import { useState } from "react";

interface DecisionExplanation {
  summary: string;
  model_reasoning: string;
  value_reasoning: string;
  policy_reasoning: string;
  final_action_reasoning: string;
  disclaimer: string;
  available: boolean;
}

/**
 * Phase 12A-12C verification hook: on-demand natural-language explanation
 * of the case's latest decision, from NVIDIA NIM. This is a read-only
 * side channel over an already-computed decision — it never re-runs or
 * influences the T-Learner / EIRV / policy result shown elsewhere on this
 * page, which remains the authoritative decision record.
 */
export function ExplanationPanel({ caseId }: { caseId: string }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [data, setData] = useState<DecisionExplanation | null>(null);

  async function fetchExplanation() {
    setState("loading");
    try {
      const base = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
      const res = await fetch(`${base}/api/recovery-cases/${caseId}/explanation`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as DecisionExplanation;
      setData(json);
      setState("done");
    } catch {
      setState("error");
    }
  }

  return (
    <div className="animate-fade-up rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            LLM Explanation
          </h2>
          <p className="mt-0.5 text-[11px] text-gray-400">
            Powered by NVIDIA NIM — narrates the decision above, does not make it.
          </p>
        </div>
        <button
          onClick={fetchExplanation}
          disabled={state === "loading"}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition-all hover:bg-gray-50 active:scale-95 disabled:opacity-50"
        >
          {state === "loading" ? "Asking NVIDIA NIM…" : "Explain this decision"}
        </button>
      </div>

      {state === "error" && (
        <p className="mt-3 text-xs text-gray-400">
          LLM explanation unavailable. The decision itself is unaffected.
        </p>
      )}

      {state === "done" && data && !data.available && (
        <p className="mt-3 text-xs text-gray-400">{data.disclaimer}</p>
      )}

      {state === "done" && data && data.available && (
        <div className="mt-3 space-y-2 text-sm text-gray-700">
          <p>{data.summary}</p>
          <dl className="space-y-1.5 border-t border-gray-100 pt-2 text-xs">
            <Row label="AI model" value={data.model_reasoning} />
            <Row label="Decision value (EIRV)" value={data.value_reasoning} />
            <Row label="Policy" value={data.policy_reasoning} />
            <Row label="Final action" value={data.final_action_reasoning} />
          </dl>
          <p className="border-t border-gray-100 pt-2 text-[11px] italic text-gray-400">
            {data.disclaimer}
          </p>
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-medium text-gray-500">{label}</dt>
      <dd className="text-gray-600">{value}</dd>
    </div>
  );
}
