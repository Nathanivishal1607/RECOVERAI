"use client";

import { useEffect, useRef, useState } from "react";
import { formatCurrency } from "@/lib/format";

export type CountUpFormat = "currency" | "percent1" | "integer";

// A function prop can't cross the server -> client boundary (it isn't
// serializable), so the format is a plain string the client resolves
// itself — never invented data, just how to render the real number.
function applyFormat(kind: CountUpFormat, n: number): string {
  switch (kind) {
    case "currency":
      return formatCurrency(n);
    case "percent1":
      return `${n.toFixed(1)}%`;
    case "integer":
      return Math.round(n).toLocaleString("en-IN");
  }
}

/**
 * Animates the display of an already-fetched real number from 0 to its
 * final value. Never fetches data itself — `value` must come from a
 * server-rendered API response. Falls back to the plain formatted value
 * immediately under prefers-reduced-motion or if animation is skipped.
 */
export function CountUp({
  value,
  format,
  durationMs = 900,
}: {
  value: number;
  format: CountUpFormat;
  durationMs?: number;
}) {
  const [display, setDisplay] = useState(value);
  const raf = useRef<number>();

  useEffect(() => {
    const reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      setDisplay(value);
      return;
    }

    const start = performance.now();
    const from = 0;

    function tick(now: number) {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplay(from + (value - from) * eased);
      if (t < 1) {
        raf.current = requestAnimationFrame(tick);
      } else {
        setDisplay(value);
      }
    }
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return <>{applyFormat(format, display)}</>;
}
