import { motion } from "framer-motion";

/** The PRISM mark: white light in, three spectral beams out (aurora · nebula · ember). */
export function PrismMark({ size = 36, animate = false }: { size?: number; animate?: boolean }) {
  const beam = (d: string, color: string, delay: number) => (
    <motion.path
      d={d}
      stroke={color}
      strokeWidth={2.6}
      strokeLinecap="round"
      initial={animate ? { pathLength: 0, opacity: 0 } : false}
      animate={{ pathLength: 1, opacity: 1 }}
      transition={{ duration: 0.7, delay, ease: "easeOut" }}
    />
  );
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="PRISM">
      <path d="M32 10 54 50H10Z" fill="rgba(232,237,247,0.04)" stroke="#E8EDF7" strokeWidth={2.6} strokeLinejoin="round" />
      {beam("M4 34 30 30", "#E8EDF7", 0)}
      {beam("M36 31 60 23", "#3EE6B0", 0.35)}
      {beam("M36 33 60 34", "#A98BFF", 0.45)}
      {beam("M36 35 60 45", "#FF8B4D", 0.55)}
    </svg>
  );
}
