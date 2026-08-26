import Modal from "../ui/Modal";
import type { ClinicalContext } from "../../types/api";
import type { DocumentInfo } from "../../types/api";

interface Props {
  open: boolean;
  onClose: () => void;
  context: ClinicalContext;
  onSave: (ctx: ClinicalContext) => void;
  documents: DocumentInfo[];
}

export default function ClinicalContextModal({
  open,
  onClose,
  context,
  onSave,
  documents,
}: Props) {
  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    onSave({
      specialty: (fd.get("specialty") as string) || undefined,
      patient_age_group: (fd.get("patient_age_group") as ClinicalContext["patient_age_group"]) || undefined,
      patient_weight_kg: fd.get("patient_weight_kg")
        ? Number(fd.get("patient_weight_kg"))
        : undefined,
      doc_names: fd.getAll("doc_names") as string[],
    });
    onClose();
  };

  return (
    <Modal open={open} onClose={onClose} title="Clinical Context" size="lg">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Specialty
            </label>
            <input
              name="specialty"
              defaultValue={context.specialty ?? ""}
              placeholder="e.g. pediatrics, cardiology"
              className="input-field"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Patient age group
            </label>
            <select
              name="patient_age_group"
              defaultValue={context.patient_age_group ?? "adult"}
              className="input-field"
            >
              <option value="pediatric">Pediatric</option>
              <option value="adult">Adult</option>
              <option value="geriatric">Geriatric</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-oky-muted">
              Patient weight (kg)
            </label>
            <input
              name="patient_weight_kg"
              type="number"
              step="0.1"
              min="0"
              defaultValue={context.patient_weight_kg ?? ""}
              placeholder="Optional"
              className="input-field"
            />
          </div>
        </div>

        {documents.length > 0 && (
          <div>
            <label className="mb-2 block text-xs font-medium text-oky-muted">
              Restrict to documents (optional)
            </label>
            <div className="max-h-40 space-y-2 overflow-y-auto rounded-lg border border-oky-border p-3">
              {documents.map((doc) => (
                <label
                  key={doc.doc_id}
                  className="flex cursor-pointer items-center gap-2 text-sm"
                >
                  <input
                    type="checkbox"
                    name="doc_names"
                    value={doc.doc_name}
                    defaultChecked={context.doc_names?.includes(doc.doc_name)}
                    className="rounded border-oky-border bg-white/80 text-oky-purple focus:ring-oky-purple"
                  />
                  <span className="truncate">{doc.doc_name}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" className="btn-primary">
            Save context
          </button>
        </div>
      </form>
    </Modal>
  );
}
