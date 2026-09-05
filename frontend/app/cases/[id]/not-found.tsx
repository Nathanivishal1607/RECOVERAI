import Link from "next/link";

export default function NotFound() {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-10 text-center">
      <h2 className="text-lg font-semibold text-gray-900">Recovery case not found</h2>
      <p className="mt-1 text-sm text-gray-500">
        This case may not exist, or the ID is invalid.
      </p>
      <Link
        href="/cases"
        className="mt-4 inline-block rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
      >
        Back to all cases
      </Link>
    </div>
  );
}
