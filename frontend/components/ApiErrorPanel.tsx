import type { ApiError } from "@/lib/api";

export function ApiErrorPanel({ error }: { error: ApiError | Error }) {
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-6">
      <h2 className="text-sm font-semibold text-red-800">Couldn&apos;t load data</h2>
      <p className="mt-1 text-sm text-red-700">{error.message}</p>
      <p className="mt-3 text-xs text-red-600">
        Check that the RecoverAI API is running and reachable at the configured
        BACKEND_API_URL.
      </p>
    </div>
  );
}
