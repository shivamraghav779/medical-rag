import { useCallback, useEffect, useState } from "react";
import {
  FileText,
  Plus,
  RefreshCw,
  Search,
  Star,
  Trash2,
} from "lucide-react";
import {
  deleteDocument,
  fetchDocuments,
  uploadDocument,
} from "../api/client";
import ErrorBanner from "../components/ui/ErrorBanner";
import type { DocumentInfo } from "../types/api";
import DeleteConfirmModal from "../components/documents/DeleteConfirmModal";
import UploadModal from "../components/documents/UploadModal";

function formatDate(ts?: number) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString();
}

// Relative format for the list ("2 days ago"), full date kept as a title
// tooltip — no new date library, this only needs whole-day granularity.
function formatRelative(ts?: number): string {
  if (!ts) return "—";
  const diffMs = Date.now() - ts * 1000;
  const days = Math.floor(diffMs / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months > 1 ? "s" : ""} ago`;
  const years = Math.floor(months / 12);
  return `${years} year${years > 1 ? "s" : ""} ago`;
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [search, setSearch] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DocumentInfo | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDocs(await fetchDocuments());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = docs.filter(
    (d) =>
      d.doc_name.toLowerCase().includes(search.toLowerCase()) ||
      d.doc_type?.toLowerCase().includes(search.toLowerCase()) ||
      d.source_org?.toLowerCase().includes(search.toLowerCase()),
  );

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteDocument(deleteTarget.doc_id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-oky-border/40 px-8 py-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-oky-text">Document Library</h1>
          <p className="mt-1 text-sm text-oky-muted">
            Upload and manage clinical PDFs for retrieval.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={load} className="btn-ghost" title="Refresh" aria-label="Refresh document list">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
          </button>
          <button type="button" onClick={() => setUploadOpen(true)} className="btn-primary">
            <Plus className="h-4 w-4" />
            Upload PDF
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-6xl">
          <div className="relative mb-4 max-w-md">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-oky-muted" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search documents..."
              className="input-field pl-9"
            />
          </div>

          {error != null && <ErrorBanner error={error} onRetry={load} className="mb-4" />}

          {loading && docs.length === 0 ? (
            <p className="text-sm text-oky-muted">Loading documents…</p>
          ) : filtered.length === 0 ? (
            <div className="card flex flex-col items-center py-16 text-center">
              <FileText className="mb-3 h-10 w-10 text-oky-muted" />
              <p className="text-sm text-oky-muted">No documents found.</p>
              <button
                type="button"
                onClick={() => setUploadOpen(true)}
                className="btn-primary mt-4"
              >
                Upload your first PDF
              </button>
            </div>
          ) : (
            <div className="card overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-oky-border bg-oky-purple/5 text-left text-xs uppercase tracking-wide text-oky-muted">
                    <th className="px-4 py-3 font-medium">Document</th>
                    <th className="px-4 py-3 font-medium">Type</th>
                    <th className="px-4 py-3 font-medium">Source</th>
                    <th className="px-4 py-3 font-medium">Authority</th>
                    <th className="px-4 py-3 font-medium">Chunks</th>
                    <th className="px-4 py-3 font-medium">Uploaded</th>
                    <th className="px-4 py-3 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((doc) => (
                    <tr
                      key={doc.doc_id}
                      className="border-b border-oky-border/50 transition hover:bg-oky-purple/5"
                    >
                      <td className="px-4 py-3">
                        <p className="font-medium">{doc.doc_name}</p>
                        {doc.parse_method && (
                          <p className="text-xs text-oky-muted">{doc.parse_method}</p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="badge bg-oky-purple/10 text-oky-purple">
                          {doc.doc_type?.replace(/_/g, " ") ?? "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-oky-muted">
                        {doc.source_org ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        {doc.authority_level != null ? (
                          <span
                            className="inline-flex items-center gap-1 text-xs font-medium text-oky-purple"
                            title={`Authority level ${doc.authority_level} of 5`}
                          >
                            {Array.from({ length: 5 }, (_, i) => (
                              <Star
                                key={i}
                                className={`h-3 w-3 ${
                                  i < doc.authority_level!
                                    ? "fill-oky-purple text-oky-purple"
                                    : "text-oky-border"
                                }`}
                              />
                            ))}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3 text-oky-muted">{doc.chunk_count}</td>
                      <td className="px-4 py-3 text-oky-muted" title={formatDate(doc.upload_timestamp)}>
                        {formatRelative(doc.upload_timestamp)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(doc)}
                          className="btn-ghost p-2 text-red-400 hover:bg-red-500/10"
                          title="Delete document"
                          aria-label={`Delete ${doc.doc_name}`}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUpload={async (file, metadata) => {
          await uploadDocument(file, metadata);
          await load();
        }}
      />

      <DeleteConfirmModal
        open={!!deleteTarget}
        docName={deleteTarget?.doc_name ?? ""}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        loading={deleting}
      />
    </div>
  );
}
