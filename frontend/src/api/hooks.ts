import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./client";
import type { BlueprintEdit, BlueprintRecord, Dataset, Job, Rows, Source } from "./types";

const busy = (s?: Source) => s && (s.status === "pending" || s.status === "processing");

export function useSources(ws: number | undefined) {
  return useQuery({
    queryKey: ["sources", ws],
    queryFn: () => api<Source[]>(`/workspaces/${ws}/sources`),
    enabled: !!ws,
    refetchInterval: (q) => ((q.state.data ?? []).some(busy) ? 1200 : false),
  });
}

export function useDataset(ws: number | undefined, id: number | undefined) {
  return useQuery({
    queryKey: ["dataset", ws, id],
    queryFn: () => api<Dataset>(`/workspaces/${ws}/datasets/${id}`),
    enabled: !!ws && !!id,
    staleTime: 60_000,
  });
}

export function useRows(ws: number | undefined, id: number | undefined, offset: number, limit: number, layer: "clean" | "raw") {
  return useQuery({
    queryKey: ["rows", ws, id, offset, limit, layer],
    queryFn: () => api<Rows>(`/workspaces/${ws}/datasets/${id}/rows?offset=${offset}&limit=${limit}&layer=${layer}`),
    enabled: !!ws && !!id,
    placeholderData: (prev) => prev,
  });
}

export function useUpload(ws: number | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api<Source>(`/workspaces/${ws}/sources`, { method: "POST", body: fd });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources", ws] }),
  });
}

export function usePaste(ws: number | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { text: string; name: string }) => api<Source>(`/workspaces/${ws}/sources/paste`, { method: "POST", json: v }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources", ws] }),
  });
}

export function useRegenerate(ws: number | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: number) => api(`/workspaces/${ws}/sources/${sourceId}/workbook`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources", ws] }),
  });
}

// ---------------- Blueprint ----------------

export function useAiStatus(ws: number | undefined) {
  return useQuery({
    queryKey: ["ai-status", ws],
    queryFn: () => api<AiStatus>(`/workspaces/${ws}/ai-status`),
    enabled: !!ws,
    staleTime: 300_000,
  });
}

export function useBlueprint(ws: number | undefined, sourceId: number | undefined) {
  return useQuery({
    queryKey: ["blueprint", ws, sourceId],
    queryFn: () => api<BlueprintRecord | null>(`/workspaces/${ws}/sources/${sourceId}/blueprint`),
    enabled: !!ws && !!sourceId,
  });
}

async function waitForJob(ws: number, job: Job): Promise<Job> {
  let j = job;
  // local AI models on CPU-only servers can take many minutes; poll for up to an hour
  for (let i = 0; i < 1900 && (j.status === "queued" || j.status === "running"); i++) {
    await new Promise((r) => setTimeout(r, i < 60 ? 1000 : 2000));
    j = await api<Job>(`/workspaces/${ws}/jobs/${j.id}`);
  }
  if (j.status === "failed") throw new ApiError(422, "job_failed", j.error ?? "The job failed.");
  if (j.status !== "succeeded") throw new ApiError(504, "job_timeout", "The job is still running. Refresh the page later to see the result.");
  return j;
}

export function useGenerateBlueprint(ws: number | undefined, sourceId: number | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (requirements: string) => {
      const job = await api<Job>(`/workspaces/${ws}/sources/${sourceId}/blueprints`, { method: "POST", json: { requirements: requirements || null } });
      return waitForJob(ws!, job);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["blueprint", ws, sourceId] }),
  });
}

export function useEditBlueprint(ws: number | undefined, sourceId: number | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, edits }: { id: number; edits: BlueprintEdit[] }) =>
      api<BlueprintRecord>(`/workspaces/${ws}/blueprints/${id}`, { method: "PATCH", json: { edits } }),
    onSuccess: (rec) => qc.setQueryData(["blueprint", ws, sourceId], rec),
  });
}

export function useApplyBlueprint(ws: number | undefined, sourceId: number | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const job = await api<Job>(`/workspaces/${ws}/blueprints/${id}/apply`, { method: "POST" });
      return waitForJob(ws!, job);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["blueprint", ws, sourceId] });
      qc.invalidateQueries({ queryKey: ["sources", ws] });
      qc.invalidateQueries({ queryKey: ["dataset", ws] });
      qc.invalidateQueries({ queryKey: ["rows", ws] });
    },
  });
}

export type AiStatus = {
  provider: "ollama" | "openai_compatible" | "anthropic" | "none";
  model: string;
  configured: boolean;
  reachable: boolean | null;
  detail: string | null;
};
