import { useRef, useState, type DragEvent } from "react";
import { useUpload, usePaste } from "../api/hooks";
import type { Source } from "../api/types";

const ACCEPT = ".xlsx,.xlsm,.xls,.csv,.tsv,.txt,.json,.jsonl,.pdf,.png,.jpg,.jpeg,.webp";
const FORMATS = ["XLSX", "XLS", "CSV", "TSV", "JSON", "PDF tables", "Images*", "Pasted text"];

export function UploadPanel({ ws, onCreated, canEdit }: { ws: number; onCreated: (s: Source) => void; canEdit: boolean }) {
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [over, setOver] = useState(false);
  const [text, setText] = useState("");
  const [name, setName] = useState("Pasted data");
  const input = useRef<HTMLInputElement>(null);
  const upload = useUpload(ws);
  const paste = usePaste(ws);
  const err = (upload.error || paste.error) as Error | null;
  const busy = upload.isPending || paste.isPending;

  if (!canEdit) {
    return <div className="note">You have view-only access to this workspace. Ask an editor to add data.</div>;
  }

  async function send(file: File) {
    const s = await upload.mutateAsync(file);
    onCreated(s);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void send(f).catch(() => undefined);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div role="tablist" aria-label="Input method" style={{ display: "flex", gap: 4 }}>
        <button role="tab" aria-selected={mode === "file"} className={`btn btn-sm ${mode === "file" ? "" : "btn-ghost"}`} onClick={() => setMode("file")}>Upload a file</button>
        <button role="tab" aria-selected={mode === "paste"} className={`btn btn-sm ${mode === "paste" ? "" : "btn-ghost"}`} onClick={() => setMode("paste")}>Paste a table</button>
      </div>
      {mode === "file" ? (
        <div
          className={`dropzone ${over ? "over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
          role="button"
          tabIndex={0}
          aria-label="Choose a file to upload or drop it here"
        >
          <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden="true"><path d="M20 27V9m0 0-7 7m7-7 7 7M8 31h24" fill="none" stroke="var(--aurora)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <h2>{busy ? "Reading…" : "Drop any data here"}</h2>
          <div className="dim">Messy sheets welcome — merged headers, Lao text, mixed dates, ₭ amounts, call durations.</div>
          <div className="formats">{FORMATS.map((f) => <span key={f} className="pill">{f}</span>)}</div>
          <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(f).catch(() => undefined); e.target.value = ""; }} />
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            paste.mutateAsync({ text, name }).then((s) => { setText(""); onCreated(s); }).catch(() => undefined);
          }}
          style={{ display: "flex", flexDirection: "column", gap: 10 }}
        >
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} aria-label="Name for the pasted data" maxLength={200} />
          <textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} placeholder={"Paste rows copied from Excel, CSV, or a Markdown table…\nສາຂາ\tຈຳນວນ\nວຽງຈັນ\t10"} aria-label="Pasted table" required />
          <div><button className="btn btn-aurora" type="submit" disabled={busy || !text.trim()}>{busy ? "Reading…" : "Interpret pasted data"}</button></div>
        </form>
      )}
      {err && <div className="error-box" role="alert">{err.message}</div>}
      <div className="faint" style={{ fontSize: 12 }}>*Images are transcribed only when the administrator has configured AI vision; transcribed values are labelled as such.</div>
    </div>
  );
}
