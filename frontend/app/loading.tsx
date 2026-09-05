export default function Loading() {
  return (
    <div className="space-y-8 animate-pulse">
      <div className="h-7 w-64 rounded bg-gray-200" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-24 rounded-xl bg-gray-100" />
        ))}
      </div>
    </div>
  );
}
