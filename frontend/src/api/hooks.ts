import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { Dataset, Rows, Source } from "./types";

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
