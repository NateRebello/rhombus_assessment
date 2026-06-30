import React, { useState, useCallback, useEffect, useRef } from "react";
import { ApiError, createJob, suggestPatterns, NormalizeMode, PiiSuggestion } from "../api/client";

interface Props {
  onJobCreated: (jobId: string, targetColumn: string) => void;
}

const PII_TYPE_LABELS: Record<string, string> = {
  email: "Email address",
  phone: "Phone number",
  ssn: "Social Security Number",
  credit_card: "Credit card",
  date: "Date",
  name: "Person name",
  ip_address: "IP address",
  url: "URL",
};

/** Parse first N rows of a plain CSV text, return column → sample values map. */
function parseCsvSamples(text: string, maxRows = 30): Record<string, string[]> {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return {};

  const splitCsvRow = (row: string): string[] => {
    const result: string[] = [];
    let current = "";
    let inQuotes = false;
    for (let i = 0; i < row.length; i++) {
      const ch = row[i];
      if (ch === '"') {
        inQuotes = !inQuotes;
      } else if (ch === "," && !inQuotes) {
        result.push(current.trim());
        current = "";
      } else {
        current += ch;
      }
    }
    result.push(current.trim());
    return result;
  };

  const headers = splitCsvRow(lines[0]);
  const samples: Record<string, string[]> = {};
  headers.forEach((h) => { samples[h] = []; });

  for (let r = 1; r < Math.min(lines.length, maxRows + 1); r++) {
    const cells = splitCsvRow(lines[r]);
    headers.forEach((h, i) => {
      const val = cells[i] ?? "";
      if (val && samples[h].length < 8) samples[h].push(val);
    });
  }

  return Object.fromEntries(Object.entries(samples).filter(([, v]) => v.length > 0));
}

export function UploadForm({ onJobCreated }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [prompt, setPrompt] = useState("");
  const [targetColumn, setTargetColumn] = useState("");
  const [replacement, setReplacement] = useState("");
  const [normalizeMode, setNormalizeMode] = useState<NormalizeMode>("none");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  // PII suggestion state
  const [suggestions, setSuggestions] = useState<PiiSuggestion[]>([]);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const suggestAbortRef = useRef<AbortController | null>(null);

  // Trigger PII suggestion whenever a CSV file is selected
  const runSuggestions = useCallback(async (selectedFile: File) => {
    if (!selectedFile.name.toLowerCase().endsWith(".csv")) {
      setSuggestions([]);
      return;
    }

    // Cancel any in-flight suggestion request
    suggestAbortRef.current?.abort();
    const abortController = new AbortController();
    suggestAbortRef.current = abortController;

    setSuggestLoading(true);
    setSuggestions([]);

    try {
      const text = await selectedFile.text();
      const columnSamples = parseCsvSamples(text);
      if (Object.keys(columnSamples).length === 0) return;

      const result = await suggestPatterns(columnSamples);
      if (!abortController.signal.aborted) {
        setSuggestions(result.suggestions ?? []);
      }
    } catch {
      // Suggestions are best-effort; never block the user
    } finally {
      if (!abortController.signal.aborted) {
        setSuggestLoading(false);
      }
    }
  }, []);

  const handleFileChange = (selectedFile: File | null) => {
    setFile(selectedFile);
    setSuggestions([]);
    if (selectedFile) runSuggestions(selectedFile);
  };

  // Clean up on unmount
  useEffect(() => () => suggestAbortRef.current?.abort(), []);

  const applySuggestion = (s: PiiSuggestion) => {
    setTargetColumn(s.column);
    setPrompt(s.suggested_prompt);
    // Phone suggestions → default to E.164 standardisation mode
    if (s.pii_type === "phone") {
      setNormalizeMode("e164");
    } else if (s.pii_type === "date") {
      setNormalizeMode("iso8601");
    } else {
      setNormalizeMode("none");
      if (!replacement) setReplacement("[REDACTED]");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;

    setSubmitting(true);
    setError(null);
    try {
      const { job_id } = await createJob({
        file,
        prompt,
        targetColumn,
        replacementValue: normalizeMode === "none" ? replacement : "",
        normalizeMode,
      });
      onJobCreated(job_id, targetColumn);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `Submission failed (${err.status}): ${err.message}`
          : "Unexpected error. Please try again."
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) handleFileChange(dropped);
  };

  return (
    <form onSubmit={handleSubmit} style={styles.form}>
      <div style={styles.formHeader}>
        <h2 style={styles.formTitle}>New Pattern-Matching Job</h2>
        <p style={styles.formSubtitle}>
          Describe a pattern in plain English — we'll generate a regex and apply it
          at scale with PySpark.
        </p>
      </div>

      {/* File drop zone */}
      <div
        style={{
          ...styles.dropZone,
          ...(dragOver ? styles.dropZoneActive : {}),
          ...(file ? styles.dropZoneHasFile : {}),
        }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => document.getElementById("file-input")?.click()}
      >
        <input
          id="file-input"
          type="file"
          accept=".csv,.xlsx,.xls"
          required
          style={{ display: "none" }}
          onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <div style={styles.dropZoneContent}>
            <span style={styles.fileIcon}>📄</span>
            <div>
              <div style={styles.fileName}>{file.name}</div>
              <div style={styles.fileSize}>
                {(file.size / 1024).toFixed(1)} KB · click to replace
              </div>
            </div>
          </div>
        ) : (
          <div style={styles.dropZoneContent}>
            <span style={styles.uploadIcon}>⬆️</span>
            <div>
              <div style={styles.dropPrimary}>Drop your CSV or Excel file here</div>
              <div style={styles.dropSecondary}>or click to browse · .csv, .xlsx, .xls</div>
            </div>
          </div>
        )}
      </div>

      {/* PII suggestion chips */}
      {(suggestLoading || suggestions.length > 0) && (
        <div style={styles.suggestSection}>
          <span style={styles.suggestLabel}>
            {suggestLoading ? "Analysing columns…" : "Detected patterns — click to prefill:"}
          </span>
          {suggestLoading ? (
            <span style={styles.suggestSpinner} />
          ) : (
            <div style={styles.chipRow}>
              {suggestions.map((s) => (
                <button
                  key={s.column}
                  type="button"
                  style={styles.chip}
                  onClick={() => applySuggestion(s)}
                  title={`${s.suggested_prompt} (confidence ${Math.round(s.confidence * 100)}%)`}
                >
                  <span style={styles.chipDot} />
                  <strong>{s.column}</strong>
                  &nbsp;·&nbsp;
                  {PII_TYPE_LABELS[s.pii_type] ?? s.pii_type}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div style={styles.row}>
        <label style={styles.label}>
          <span style={styles.labelText}>Column to process</span>
          <input
            type="text"
            placeholder="e.g. email"
            required
            value={targetColumn}
            onChange={(e) => setTargetColumn(e.target.value)}
            style={styles.input}
          />
        </label>

        <label style={styles.label}>
          <span style={styles.labelText}>Transform mode</span>
          <select
            value={normalizeMode}
            onChange={(e) => setNormalizeMode(e.target.value as NormalizeMode)}
            style={styles.input}
          >
            <option value="none">Replace with fixed value</option>
            <option value="e164">Standardise → E.164 phone</option>
            <option value="iso8601">Standardise → ISO 8601 date</option>
          </select>
        </label>
      </div>

      {/* Replacement field — only shown in "none" (literal replace) mode */}
      {normalizeMode === "none" && (
        <label style={styles.label}>
          <span style={styles.labelText}>
            Replacement value{" "}
            <span style={styles.optional}>optional</span>
          </span>
          <input
            type="text"
            placeholder="e.g. [REDACTED]"
            value={replacement}
            onChange={(e) => setReplacement(e.target.value)}
            style={styles.input}
          />
        </label>
      )}

      {normalizeMode !== "none" && (
        <div style={styles.normalizeNote}>
          <strong>Standardise mode:</strong>{" "}
          {normalizeMode === "e164"
            ? "Matched cells will be converted to E.164 format (+15551234567). No replacement string needed."
            : "Matched cells will be converted to ISO 8601 (YYYY-MM-DD). No replacement string needed."}
        </div>
      )}

      <label style={{ ...styles.label, marginBottom: 0 }}>
        <span style={styles.labelText}>Describe the pattern</span>
        <textarea
          rows={3}
          placeholder={
            normalizeMode === "e164"
              ? "e.g. Find US phone numbers in any format"
              : normalizeMode === "iso8601"
              ? "e.g. Find dates in MM/DD/YYYY format"
              : "e.g. Find all email addresses ending in @gmail.com"
          }
          required
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          style={{ ...styles.input, resize: "vertical", lineHeight: 1.6 }}
        />
      </label>

      {error && (
        <div style={styles.errorBox}>
          <span style={styles.errorIcon}>⚠</span>
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={submitting || !file}
        style={{
          ...styles.submitBtn,
          opacity: submitting || !file ? 0.55 : 1,
          cursor: submitting || !file ? "not-allowed" : "pointer",
        }}
      >
        {submitting ? (
          <span style={styles.btnInner}>
            <span style={styles.spinner} /> Submitting…
          </span>
        ) : (
          <span style={styles.btnInner}>Run Job</span>
        )}
      </button>
    </form>
  );
}

const styles: Record<string, React.CSSProperties> = {
  form: {
    background: "#ffffff",
    borderRadius: 20,
    padding: "2rem",
    boxShadow: "0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.06)",
    border: "1px solid #e2e8f0",
    maxWidth: 560,
    width: "100%",
    display: "flex",
    flexDirection: "column",
    gap: "1.25rem",
  },
  formHeader: {
    borderBottom: "1px solid #f1f5f9",
    paddingBottom: "1.25rem",
  },
  formTitle: {
    fontSize: "1.125rem",
    fontWeight: 700,
    color: "#0f172a",
    marginBottom: "0.35rem",
  },
  formSubtitle: {
    fontSize: 13,
    color: "#64748b",
    margin: 0,
    lineHeight: 1.5,
  },
  dropZone: {
    border: "2px dashed #cbd5e1",
    borderRadius: 12,
    padding: "1.5rem 1rem",
    cursor: "pointer",
    transition: "border-color 0.15s, background 0.15s",
    background: "#f8fafc",
  },
  dropZoneActive: {
    borderColor: "#6366f1",
    background: "#eef2ff",
  },
  dropZoneHasFile: {
    borderColor: "#10b981",
    borderStyle: "solid",
    background: "#f0fdf4",
  },
  dropZoneContent: {
    display: "flex",
    alignItems: "center",
    gap: "0.875rem",
    pointerEvents: "none",
  },
  uploadIcon: { fontSize: 24 },
  fileIcon: { fontSize: 24 },
  dropPrimary: { fontWeight: 600, fontSize: 14, color: "#374151" },
  dropSecondary: { fontSize: 12, color: "#9ca3af", marginTop: 2 },
  fileName: { fontWeight: 600, fontSize: 14, color: "#065f46" },
  fileSize: { fontSize: 12, color: "#6b7280", marginTop: 2 },
  suggestSection: {
    background: "#f0f9ff",
    border: "1px solid #bae6fd",
    borderRadius: 10,
    padding: "0.75rem 1rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
  },
  suggestLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: "#0369a1",
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
  },
  suggestSpinner: {
    display: "inline-block",
    width: 16,
    height: 16,
    border: "2px solid #bae6fd",
    borderTopColor: "#0ea5e9",
    borderRadius: "50%",
    animation: "spin 0.7s linear infinite",
  },
  chipRow: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "0.5rem",
  },
  chip: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.4rem",
    padding: "0.35rem 0.75rem",
    background: "#fff",
    border: "1.5px solid #7dd3fc",
    borderRadius: 999,
    fontSize: 13,
    color: "#0c4a6e",
    cursor: "pointer",
    transition: "background 0.15s, border-color 0.15s",
    fontFamily: "inherit",
  },
  chipDot: {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: "#0ea5e9",
    flexShrink: 0,
  },
  row: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "0.875rem",
  },
  label: {
    display: "flex",
    flexDirection: "column",
    gap: "0.375rem",
  },
  labelText: {
    fontSize: 13,
    fontWeight: 600,
    color: "#374151",
    display: "flex",
    alignItems: "center",
    gap: "0.375rem",
  },
  optional: {
    fontSize: 11,
    fontWeight: 500,
    color: "#9ca3af",
    background: "#f3f4f6",
    borderRadius: 4,
    padding: "0.1rem 0.35rem",
  },
  normalizeNote: {
    fontSize: 13,
    color: "#475569",
    background: "#f8fafc",
    border: "1px solid #e2e8f0",
    borderRadius: 8,
    padding: "0.6rem 0.875rem",
    lineHeight: 1.5,
  },
  input: {
    padding: "0.55rem 0.75rem",
    border: "1.5px solid #e2e8f0",
    borderRadius: 8,
    fontSize: 14,
    fontWeight: 400,
    color: "#0f172a",
    background: "#fff",
    outline: "none",
    transition: "border-color 0.15s",
    width: "100%",
  },
  errorBox: {
    display: "flex",
    alignItems: "flex-start",
    gap: "0.5rem",
    color: "#dc2626",
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: 8,
    padding: "0.65rem 0.875rem",
    fontSize: 13,
    lineHeight: 1.5,
  },
  errorIcon: {
    flexShrink: 0,
    marginTop: 1,
  },
  submitBtn: {
    width: "100%",
    padding: "0.75rem 1rem",
    background: "linear-gradient(135deg, #4f46e5, #7c3aed)",
    color: "#fff",
    border: "none",
    borderRadius: 10,
    fontWeight: 700,
    fontSize: 15,
    transition: "opacity 0.15s, transform 0.1s",
    letterSpacing: 0.2,
  },
  btnInner: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.5rem",
  },
  spinner: {
    display: "inline-block",
    width: 14,
    height: 14,
    border: "2px solid rgba(255,255,255,0.3)",
    borderTopColor: "#fff",
    borderRadius: "50%",
    animation: "spin 0.7s linear infinite",
  },
};
