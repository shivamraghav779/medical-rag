import { useEffect, useRef, useState } from "react";
import { FileText, Loader2, Upload } from "lucide-react";
import { errorMessage, uploadDocument } from "../../api/client";
import type { DocumentInfo } from "../../types/api";
import Modal from "../ui/Modal";

interface Props {
  open: boolean;
  onClose: () => void;
  documents: DocumentInfo[];
  attachedDocNames: string[];
  onAttach: (docNames: string[]) => void;
  onDocumentsChange: () => void;
}

export default function AttachModal({
  open,
  onClose,
  documents,
  attachedDocNames,
  onAttach,
  onDocumentsChange,
}: Props) {
  const [tab, setTab] = useState<"library" | "upload">("library");
  const [selected, setSelected] = useState<string[]>(attachedDocNames);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setSelected(attachedDocNames);
      setTab("library");
      setFile(null);
      setError(null);
      setUploading(false);
    }
  }, [open, attachedDocNames]);

  const toggleDoc = (name: string) => {
    setSelected((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  };

  const handleUpload = async () => {
    if (!file) {
      setError("Select a PDF to upload.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const result = await uploadDocument(file, { doc_type: "clinical_guideline" });
      onDocumentsChange();
      setSelected((prev) =>
        prev.includes(result.doc_name) ? prev : [...prev, result.doc_name],
      );
      setFile(null);
      setTab("library");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  const handleSave = () => {
    onAttach(selected);
    onClose();
  };

  return (
    <Modal open={open} onClose={onClose} title="Attach documents" size="lg">
      <div className="mb-4 flex gap-2">
        <button
          type="button"
          onClick={() => setTab("library")}
          className={`rounded-xl px-4 py-2 text-sm font-medium transition ${
            tab === "library"
              ? "bg-oky-purple/10 text-oky-purple"
              : "text-oky-muted hover:bg-oky-purple/5"
          }`}
        >
          From library
        </button>
        <button
          type="button"
          onClick={() => setTab("upload")}
          className={`rounded-xl px-4 py-2 text-sm font-medium transition ${
            tab === "upload"
              ? "bg-oky-purple/10 text-oky-purple"
              : "text-oky-muted hover:bg-oky-purple/5"
          }`}
        >
          Upload PDF
        </button>
      </div>

      {tab === "library" ? (
        <div className="space-y-3">
          <p className="text-sm text-oky-muted">
            Select documents to scope retrieval for your next message.
          </p>
          {documents.length === 0 ? (
            <p className="rounded-xl bg-oky-purple/5 px-4 py-6 text-center text-sm text-oky-muted">
              No documents yet. Upload a PDF first.
            </p>
          ) : (
            <div className="max-h-64 space-y-1 overflow-y-auto">
              {documents.map((doc) => (
                <label
                  key={doc.doc_id}
                  className="flex cursor-pointer items-center gap-3 rounded-xl border border-oky-border/50 px-3 py-2.5 transition hover:bg-oky-purple/5"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(doc.doc_name)}
                    onChange={() => toggleDoc(doc.doc_name)}
                    className="rounded border-oky-border text-oky-purple focus:ring-oky-purple"
                  />
                  <FileText className="h-4 w-4 shrink-0 text-oky-purple" />
                  <span className="min-w-0 flex-1 truncate text-sm">{doc.doc_name}</span>
                  <span className="text-xs text-oky-muted">{doc.chunk_count} chunks</span>
                </label>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-oky-muted">
            Upload a clinical PDF — it will be indexed and available to attach.
          </p>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed border-oky-border/60 px-4 py-8 text-sm text-oky-muted transition hover:border-oky-purple/40 hover:bg-oky-purple/5"
          >
            <Upload className="h-8 w-8 text-oky-purple/50" />
            {file ? file.name : "Click to choose PDF (max 25 MB)"}
          </button>
          <button
            type="button"
            onClick={handleUpload}
            disabled={!file || uploading}
            className="btn-primary w-full"
          >
            {uploading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Uploading…
              </>
            ) : (
              "Upload & attach"
            )}
          </button>
        </div>
      )}

      {error && <p className="mt-3 text-sm text-red-500">{error}</p>}

      <div className="mt-6 flex justify-end gap-2">
        <button type="button" onClick={onClose} className="btn-secondary">
          Cancel
        </button>
        <button type="button" onClick={handleSave} className="btn-primary">
          Attach {selected.length > 0 ? `(${selected.length})` : ""}
        </button>
      </div>
    </Modal>
  );
}
