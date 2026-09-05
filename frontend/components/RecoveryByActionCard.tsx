import type { RecoveryByAction } from "@/lib/types";
import { ActionBadge } from "@/components/Badge";

const ORDER = ["RETRY", "MESSAGE", "NO_ACTION"] as const;

export function RecoveryByActionCard({ data }: { data: RecoveryByAction }) {
  return (
    <div className="animate-fade-up rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <h2 className="text-sm font-semibold text-gray-900">Observed Outcomes by Final Action</h2>
      <p className="mt-1 text-xs text-gray-500">
        Real recorded outcomes, not a causal/uplift estimate of AI effectiveness.
      </p>
      <div className="mt-4 space-y-4">
        {ORDER.map((action) => {
          const bucket = data[action];
          const total = bucket.recovered + bucket.not_recovered;
          const recoveredPct = total > 0 ? (bucket.recovered / total) * 100 : 0;
          return (
            <div key={action}>
              <div className="mb-1 flex items-center justify-between text-xs">
                <ActionBadge action={action} />
                <span className="text-gray-500">
                  {bucket.recovered.toLocaleString("en-IN")} recovered ·{" "}
                  {bucket.not_recovered.toLocaleString("en-IN")} not recovered
                </span>
              </div>
              <div className="flex h-2 w-full overflow-hidden rounded-full bg-gray-100">
                <div
                  className="h-full bg-emerald-500"
                  style={{ width: `${recoveredPct}%` }}
                  title={`${recoveredPct.toFixed(0)}% recovered`}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
