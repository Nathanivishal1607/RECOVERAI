export default function Loading() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="h-7 w-48 rounded bg-gray-200" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="h-40 rounded-xl bg-gray-100" />
        <div className="h-40 rounded-xl bg-gray-100" />
      </div>
      <div className="h-64 rounded-xl bg-gray-100" />
    </div>
  );
}
