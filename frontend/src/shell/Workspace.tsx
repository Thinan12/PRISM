import { useEffect, useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { motion, useReducedMotion } from "framer-motion";
import { api, downloadFile } from "../api/client";
import { useRegenerate, useSources } from "../api/hooks";
import type { Dataset, Source } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { CommandPalette, type Command } from "../components/CommandPalette";
import { BlueprintView } from "../components/BlueprintView";
import { DataPreview } from "../components/DataPreview";
import { Inspector } from "../components/Inspector";
import { PrismMark } from "../components/PrismMark";
import { QualityView } from "../components/QualityView";
import { SchemaMap, type Selection } from "../components/SchemaMap";
import { SourceExplorer, StatusPill } from "../components/SourceExplorer";
import { UploadPanel } from "../components/UploadPanel";
import { fmtBytes, fmtInt } from "../lib/format";

type Tab = "map" | "blueprint" | "data" | "quality";

export function Workspace() {
  const { user, logout } = useAuth();
  const [wsId, setWsId] = useState<number | undefined>(user?.workspaces[0]?.id);
  const ws = user?.workspaces.find((w) => w.id === wsId);
  const canEdit = ws?.role === "admin" || ws?.role === "editor";
  const { data: sources = [], error: sourcesError, isSuccess: sourcesLoaded } = useSources(wsId);
  const [sourceId, setSourceId] = useState<number>();
  const [datasetId, setDatasetId] = useState<number>();
  const [tab, setTab] = useState<Tab>("map");
  const [selection, setSelection] = useState<Selection>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const regenerate = useRegenerate(wsId);
  const reduce = useReducedMotion();

  const source: Source | undefined = sources.find((s) => s.id === sourceId);

  // pick the newest source by default
  useEffect(() => {
    if (sourceId === undefined && sources.length) setSourceId(sources[0].id);
  }, [sources, sourceId]);

  const dsQueries = useQueries({
    queries: (source?.status === "ready" ? source.datasets : []).map((d) => ({
      queryKey: ["dataset", wsId, d.id, d.current_version],
      queryFn: () => api<Dataset>(`/workspaces/${wsId}/datasets/${d.id}`),
      staleTime: 60_000,
    })),
  });
  const datasets = dsQueries.map((q) => q.data).filter((d): d is Dataset => !!d);
  const dsLoading = dsQueries.some((q) => q.isLoading);
  const activeDs = datasets.find((d) => d.id === datasetId) ?? datasets[0];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function selectSource(id: number, dsId?: number) {
    setSourceId(id);
    setDatasetId(dsId);
    setAdding(false);
    if (dsId) {
      setSelection({ kind: "dataset", datasetId: dsId });
      if (tab === "map") setTab("data");
    } else {
      setSelection(null);
      setTab("map");
    }
  }

  const latestWorkbook = source?.artifacts[0];
  const download = () => {
    if (wsId && latestWorkbook) void downloadFile(`/workspaces/${wsId}/artifacts/${latestWorkbook.id}/download`, latestWorkbook.filename);
  };

  const commands: Command[] = useMemo(() => {
    const cmds: Command[] = [];
    if (canEdit) cmds.push({ id: "add", group: "Actions", label: "Add data — upload or paste", hint: "file · paste", run: () => setAdding(true) });
    if (latestWorkbook) cmds.push({ id: "dl", group: "Actions", label: `Download workbook — ${latestWorkbook.filename}`, run: download });
    if (source) {
      cmds.push({ id: "t-map", group: "View", label: "Schema Map", run: () => setTab("map") });
      cmds.push({ id: "t-bp", group: "View", label: "Blueprint", run: () => setTab("blueprint") });
      if (datasets.length) {
        cmds.push({ id: "t-data", group: "View", label: "Data", run: () => setTab("data") });
        cmds.push({ id: "t-q", group: "View", label: "Quality", run: () => setTab("quality") });
      }
    }
    for (const s of sources) {
      cmds.push({ id: `s${s.id}`, group: "Sources", label: s.filename, hint: s.status, run: () => selectSource(s.id) });
      for (const d of s.datasets) cmds.push({ id: `d${d.id}`, group: "Tables", label: d.name, hint: `${fmtInt(d.row_count)} rows`, run: () => selectSource(s.id, d.id) });
    }
    for (const w of user?.workspaces ?? []) if (w.id !== wsId) cmds.push({ id: `w${w.id}`, group: "Workspaces", label: `Switch to ${w.name}`, run: () => { setWsId(w.id); setSourceId(undefined); } });
    cmds.push({ id: "out", group: "Account", label: "Sign out", run: logout });
    return cmds;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources, source, datasets.length, canEdit, latestWorkbook, user, wsId]);

  // only an empty workspace that has finished loading shows the upload hero (no flash while loading)
  const showUpload = adding || (sourcesLoaded && sources.length === 0);

  return (
    <div className={`shell ${tab !== "map" || showUpload ? "no-inspector" : ""}`}>
      <nav className="rail" aria-label="Workspaces">
        <div className="rail-logo"><PrismMark size={36} /></div>
        {user?.workspaces.map((w) => (
          <button key={w.id} className={`rail-btn ${w.id === wsId ? "active" : ""}`} title={`${w.name} (${w.role})`} aria-label={`Workspace ${w.name}`} onClick={() => { setWsId(w.id); setSourceId(undefined); setSelection(null); }}>
            {w.name.slice(0, 2).toUpperCase()}
          </button>
        ))}
        <div className="rail-spacer" />
        <button className="rail-btn" title={`Sign out ${user?.email}`} aria-label="Sign out" onClick={logout}>⏻</button>
      </nav>

      <header className="topbar">
        <div className="crumbs">
          <span className="dim">{ws?.name}</span>
          {ws && <span className="pill">{ws.role}</span>}
          {source && !showUpload && <><span className="faint">/</span><span>{source.filename}</span><StatusPill status={source.status} /></>}
        </div>
        {source?.status === "ready" && !showUpload && (
          <div style={{ display: "flex", gap: 8, marginLeft: 16 }}>
            {latestWorkbook && <button className="btn btn-sm btn-aurora" onClick={download} title={`${latestWorkbook.filename} · ${fmtBytes(latestWorkbook.size_bytes)}`}>↓ Excel workbook</button>}
            {canEdit && <button className="btn btn-sm" disabled={regenerate.isPending} onClick={() => regenerate.mutate(source.id)}>{regenerate.isPending ? "Rebuilding…" : "Rebuild"}</button>}
          </div>
        )}
        <button className="cmd-trigger" onClick={() => setPaletteOpen(true)} aria-label="Open command bar">
          <span>Search or run a command</span><span className="kbd">Ctrl K</span>
        </button>
      </header>

      <SourceExplorer sources={sources} activeSource={sourceId} activeDataset={datasetId} onSelect={selectSource} onAdd={() => setAdding(true)} canEdit={canEdit} />

      <main className="canvas">
        {sourcesError && <div className="error-box" style={{ margin: 16 }}>{(sourcesError as Error).message}</div>}
        {showUpload && wsId ? (
          <div className="canvas-body">
            <motion.div className="section" style={{ maxWidth: 760, margin: "6vh auto 0" }} initial={reduce ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
              <h1 className="display" style={{ fontSize: 34, margin: "0 0 6px" }}>What should PRISM read?</h1>
              <p className="dim" style={{ marginTop: 0 }}>PRISM finds the tables, headers and types, profiles every column deterministically, and builds a professional Excel workbook.</p>
              <UploadPanel ws={wsId} canEdit={canEdit} onCreated={(s) => { setAdding(false); selectSource(s.id); }} />
              {adding && sources.length > 0 && <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={() => setAdding(false)}>Cancel</button>}
            </motion.div>
          </div>
        ) : !sourcesLoaded && !sourcesError ? (
          <div className="faint" style={{ padding: 24 }} role="status">Loading workspace…</div>
        ) : source ? (
          <>
            {source.status === "ready" && (
              <div className="canvas-tabs" role="tablist">
                {([["map", "Schema Map"], ["blueprint", "Blueprint"], ["data", "Data"], ["quality", "Quality"]] as [Tab, string][]).map(([k, l]) => (
                  <button key={k} role="tab" className="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>
                ))}
                {(tab === "data" || tab === "quality") && datasets.length > 1 && (
                  <select className="input" style={{ width: "auto", marginLeft: "auto", height: 30, padding: "0 8px", alignSelf: "center" }} value={activeDs?.id} onChange={(e) => setDatasetId(Number(e.target.value))} aria-label="Table">
                    {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                )}
              </div>
            )}
            <div className="canvas-body">
              {(source.status === "pending" || source.status === "processing") && (
                <div className="reading">
                  <div>
                    <PrismMark size={72} animate={!reduce} />
                    <h2 className="display" style={{ margin: "14px 0 4px" }}>Reading {source.filename}</h2>
                    <p className="dim">Detecting tables and headers · typing columns · profiling · building the workbook</p>
                  </div>
                </div>
              )}
              {source.status === "failed" && (
                <div className="section" style={{ maxWidth: 680 }}>
                  <h2 className="display">PRISM could not read this source</h2>
                  <div className="error-box">{source.error}</div>
                  {canEdit && <button className="btn" style={{ marginTop: 14 }} onClick={() => setAdding(true)}>Try another file</button>}
                </div>
              )}
              {source.status === "ready" && dsLoading && <div className="faint" style={{ padding: 24 }}>Loading tables…</div>}
              {source.status === "ready" && !dsLoading && tab === "map" && <SchemaMap source={source} datasets={datasets} selection={selection} onSelect={setSelection} />}
              {source.status === "ready" && tab === "data" && activeDs && wsId && <DataPreview key={activeDs.id} ws={wsId} ds={activeDs} />}
              {source.status === "ready" && tab === "quality" && activeDs && <QualityView ds={activeDs} />}
              {source.status === "ready" && tab === "blueprint" && wsId && <BlueprintView key={source.id} ws={wsId} source={source} canEdit={canEdit} />}
            </div>
          </>
        ) : null}
      </main>

      {tab === "map" && !showUpload && (
        <aside className="inspector" aria-label="Inspector">
          <div className="panel-head"><span>Inspector</span></div>
          <Inspector selection={selection} source={source} datasets={datasets} />
        </aside>
      )}

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
    </div>
  );
}
