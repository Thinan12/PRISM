import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

export interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
}

export function CommandPalette({ open, onClose, commands }: { open: boolean; onClose: () => void; commands: Command[] }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const reduce = useReducedMotion();

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle ? commands.filter((c) => `${c.label} ${c.group} ${c.hint ?? ""}`.toLowerCase().includes(needle)) : commands;
  }, [q, commands]);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => setActive(0), [q]);

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") onClose();
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && results[active]) { e.preventDefault(); results[active].run(); onClose(); }
  }

  let lastGroup = "";
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="overlay" onMouseDown={onClose} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduce ? 0 : 0.12 }}>
          <motion.div
            className="palette hull"
            role="dialog"
            aria-modal="true"
            aria-label="Command bar"
            onMouseDown={(e) => e.stopPropagation()}
            initial={reduce ? false : { y: -8, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -8, opacity: 0 }}
            transition={{ duration: 0.15 }}
          >
            <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder="Type a command or search sources and tables…" aria-label="Command" />
            <ul role="listbox">
              {results.length === 0 && <li className="faint" style={{ padding: 14 }}>No matches.</li>}
              {results.map((c, i) => {
                const header = c.group !== lastGroup ? c.group : null;
                lastGroup = c.group;
                return (
                  <li key={c.id}>
                    {header && <div className="group">{header}</div>}
                    <button data-active={i === active} role="option" aria-selected={i === active} onMouseEnter={() => setActive(i)} onClick={() => { c.run(); onClose(); }}>
                      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.label}</span>
                      {c.hint && <span className="faint" style={{ fontSize: 12 }}>{c.hint}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
