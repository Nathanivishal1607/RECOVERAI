"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-6">
      <h2 className="text-sm font-semibold text-red-800">Something went wrong</h2>
      <p className="mt-1 text-sm text-red-700">{error.message}</p>
      <button
        onClick={() => reset()}
        className="mt-4 rounded-lg bg-red-800 px-4 py-2 text-sm font-medium text-white hover:bg-red-900"
      >
        Try again
      </button>
    </div>
  );
}
