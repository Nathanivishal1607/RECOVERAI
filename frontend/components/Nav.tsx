import Link from "next/link";

export function Nav() {
  return (
    <header className="border-b border-gray-200 bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gray-900 text-sm font-bold text-white">
            R
          </span>
          <span className="text-lg font-semibold tracking-tight text-gray-900">RecoverAI</span>
        </Link>
        <nav className="flex gap-6 text-sm font-medium text-gray-600">
          <Link href="/" className="hover:text-gray-900">
            Dashboard
          </Link>
          <Link href="/cases" className="hover:text-gray-900">
            Recovery Cases
          </Link>
        </nav>
      </div>
    </header>
  );
}
