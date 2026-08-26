import { FileText, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { DOC_TYPES, type DocType } from "../../types/api";
import type { UploadMetadata } from "../../api/client";
import { errorMessage } from "../../api/client";
import Modal from "../ui/Modal";

interface Props {
  open: boolean;
  onClose: () => void;
  onUpload: (file: File, metadata: UploadMetadata) => Promise<void>;
}

const TYPE_FIELDS: Record<string, { name: string; label: string; placeholder?: string }[]> = {
  clinical_guideline: [
    { name: "guideline_version", label: "Guideline version" },
    { name: "issuing_body", label: "Issuing body" },
    { name: "disease_area", label: "Disease area" },
  ],
  drug_monograph: [
    { name: "drug_generic_name", label: "Generic name" },
    { name: "drug_class", label: "Drug class" },
    { name: "atc_code", label: "ATC code" },
  ],
  treatment_protocol: [{ name: "disease_area", label: "Disease area" }],
  diagnostic_criteria: [
    { name: "condition_name", label: "Condition name" },
    { name: "criteria_system", label: "Criteria system", placeholder: "DSM-5, ICD-11" },
  ],
};

export default function UploadModal({ open, onClose, onUpload }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState<DocType>("clinical_guideline");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setError(null);
      setLoading(false);
      setDragOver(false);
    }
  }, [open]);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!file) {
      setError("Please select a PDF file.");
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      setError("File exceeds 25 MB limit.");
      return;
    }

    setLoading(true);
    setError(null);
    const fd = new FormData(e.currentTarget);
    const metadata: UploadMetadata = {
      doc_type: docType,
      source_org: (fd.get("source_org") as string) || undefined,
      authority_level: fd.get("authority_level")
        ? Number(fd.get("authority_level"))
        : undefined,
      version: (fd.get("version") as string) || undefined,
      publication_year: fd.get("publication_year")
        ? Number(fd.get("publication_year"))
        : undefined,
    };

    TYPE_FIELDS[docType]?.forEach(({ name }) => {
      const val = fd.get(name) as string;
      if (val) (metadata as Record<string, unknown>)[name] = val;
    });

    try {
      await onUpload(file, metadata);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Upload Document" size="xl">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-oky-muted">
            PDF file *
          </label>
          {/* Proper drop zone (click or drag) instead of a bare file input —
              UX_AUDIT.md: "must be a proper drag-and-drop zone with clear
              visual feedback on hover and during upload." The native input
              stays, just visually hidden, so file-picker semantics/a11y are
              unchanged. */}
          <div
            role="button"
            tabIndex={0}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const dropped = e.dataTransfer.files?.[0];
              if (dropped) setFile(dropped);
            }}
            className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition ${
              dragOver
                ? "border-oky-purple bg-oky-purple/10"
                : file
                  ? "border-oky-purple/40 bg-oky-purple/5"
                  : "border-oky-border hover:border-oky-purple/40 hover:bg-oky-purple/5"
            }`}
          >
            {file ? (
              <>
                <FileText className="h-8 w-8 text-oky-purple" />
                <p className="text-sm font-medium text-oky-text">{file.name}</p>
                <p className="text-xs text-oky-muted">
                  {(file.size / (1024 * 1024)).toFixed(1)} MB — click or drop to replace
                </p>
              </>
            ) : (
              <>
                <UploadCloud className={`h-8 w-8 ${dragOver ? "text-oky-purple" : "text-oky-muted"}`} />
                <p className="text-sm font-medium text-oky-text">
                  {dragOver ? "Drop the PDF here" : "Drag a PDF here, or click to browse"}
                </p>
                <p className="text-xs text-oky-muted">Up to 25 MB</p>
              </>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Document type
            </label>
            <select
              value={docType}
              onChange={(e) => setDocType(e.target.value as DocType)}
              className="input-field"
            >
              {DOC_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Source organization
            </label>
            <input name="source_org" placeholder="WHO, FDA, etc." className="input-field" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Authority level (1–5)
            </label>
            <input
              name="authority_level"
              type="number"
              min={1}
              max={5}
              defaultValue={3}
              className="input-field"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Publication year
            </label>
            <input name="publication_year" type="number" className="input-field" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Version
            </label>
            <input name="version" className="input-field" />
          </div>
        </div>

        {TYPE_FIELDS[docType] && (
          <div className="grid gap-4 sm:grid-cols-2">
            {TYPE_FIELDS[docType].map((field) => (
              <div key={field.name}>
                <label className="mb-1 block text-xs font-medium text-oky-muted">
                  {field.label}
                </label>
                <input
                  name={field.name}
                  placeholder={field.placeholder}
                  className="input-field"
                />
              </div>
            ))}
          </div>
        )}

        {loading && (
          // Indeterminate — the upload call has no byte-level progress
          // events wired up, so a determinate percentage would be fabricated.
          <div className="h-1 w-full overflow-hidden rounded-full bg-oky-purple/10">
            <div className="h-full w-1/3 animate-pulse-soft rounded-full bg-oky-purple" />
          </div>
        )}

        {error && (
          <p className="text-sm text-red-400">{error}</p>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="btn-secondary" disabled={loading}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? "Uploading…" : "Upload & index"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
