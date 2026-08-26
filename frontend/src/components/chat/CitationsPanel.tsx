import { BookOpen, X } from "lucide-react";
import type { RetrievedChunk } from "../../types/api";

interface Props {
  chunks: RetrievedChunk[];
  onClose: () => void;
}

export default function CitationsPanel({ chunks, onClose }: Props) {
  return (
    <aside className="glass-card flex w-80 shrink-0 flex-col border-l-0 m-3 ml-0 rounded-2xl">
      <div className="flex items-center justify-between border-b border-oky-border/50 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-oky-text">
          <BookOpen className="h-4 w-4 text-oky-purple" />
          Sources ({chunks.length})
        </div>
        <button type="button" onClick={onClose} className="btn-ghost p-1">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {chunks.map((chunk) => (
          <div key={chunk.chunk_id} className="rounded-xl bg-white/80 p-3 shadow-sm ring-1 ring-oky-border/40">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="badge bg-oky-purple/10 text-oky-purple">[{chunk.rank}]</span>
              <span className="truncate text-xs text-oky-muted">
                {chunk.doc_name} · p.{chunk.page_number}
              </span>
            </div>
            <p className="line-clamp-4 text-xs leading-relaxed text-oky-text-secondary">
              {chunk.text}
            </p>
          </div>
        ))}
      </div>
    </aside>
  );
}
