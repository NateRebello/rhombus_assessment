import React, { useState } from "react";
import { JobStatus } from "../api/client";

interface Props {
  status: JobStatus;
  progress: number;
  generatedRegex?: string;
  errorMessage?: string;
}

const STATUS_META: Record<
  JobStatus,
  { label: string; color: string; bg: string; textColor: string; trackColor: string }
> = {
  QUEUED:    { label: "Queued",    color: "#6366f1", bg: "#eef2ff", textColor: "#4338ca", trackColor: "#c7d2fe" },
  RUNNING:   { label: "Running",   color: "#f59e0b", bg: "#fffbeb", textColor: "#b45309", trackColor: "#fde68a" },
  SUCCESS:   { label: "Complete",  color: "#10b981", bg: "#f0fdf4", textColor: "#065f46", trackColor: "#a7f3d0" },
  FAILED:    { label: "Failed",    color: "#ef4444", bg: "#fef2f2", textColor: "#b91c1c", trackColor: "#fecaca" },
  CANCELLED: { label: "Cancelled", color: "#94a3b8", bg: "#f8fafc", textColor: "#475569", trackColor: "#e2e8f0" },
};

export function ProgressBar({ status, progress, generatedRegex, errorMessage }: Props) {
  const [accordionOpen, setAccordionOpen] = useState(false);
  const meta = STATUS_META[status];

  const isTerminal = status === "SUCCESS" || status === "FAILED" || status === "CANCELLED";

  return (
    <div style={styles.card}>
      <div style={styles.topRow}>
        <span style={{ ...styles.badge, background: meta.bg, color: meta.textColor }}>
          <span
            style={{
              ...styles.badgeDot,
              background: meta.color,
              animation: status === "RUNNING" ? "pulse 1.5s infinite" : "none",
            }}
          />
          {meta.label}
        </span>
        <span style={{ ...styles.pct, color: meta.color }}>{progress}%</span>
      </div>

      <div style={{ ...styles.track, background: meta.trackColor }}>
        <div
          style={{
            ...styles.fill,
            width: `${progress}%`,
            background: meta.color,
            transition: status === "RUNNING" ? "width 0.5s ease" : "none",
          }}
        />
      </div>

      {status === "RUNNING" && (
        <p style={styles.hint}>
          Processing your Data, this may take a moment…
        </p>
      )}

      {errorMessage && (
        <div style={styles.errorBox}>
          <div style={styles.errorLabel}>Error</div>
          <div style={styles.errorText}>{errorMessage}</div>
        </div>
      )}

      {isTerminal && generatedRegex && (
        <div style={styles.accordion}>
          <button
            style={styles.accordionTrigger}
            onClick={() => setAccordionOpen((v) => !v)}
            type="button"
          >
            <span style={styles.accordionLabel}>
              <span style={styles.accordionIcon}>⚙</span>
              Technical Details
            </span>
            <span
              style={{
                ...styles.chevron,
                transform: accordionOpen ? "rotate(180deg)" : "rotate(0deg)",
              }}
            >
              ▾
            </span>
          </button>

          <div
            style={{
              ...styles.accordionBody,
              maxHeight: accordionOpen ? 200 : 0,
              opacity: accordionOpen ? 1 : 0,
            }}
          >
            <div style={styles.accordionInner}>
              <span style={styles.regexLabel}>Generated regex</span>
              <code style={styles.regexCode}>{generatedRegex}</code>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: "#ffffff",
    borderRadius: 16,
    padding: "1.5rem",
    boxShadow: "0 1px 3px rgba(0,0,0,0.05), 0 4px 16px rgba(0,0,0,0.05)",
    border: "1px solid #e2e8f0",
    width: "100%",
    display: "flex",
    flexDirection: "column",
    gap: "0.875rem",
  },
  topRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  badge: {
    display: "inline-flex",
    alignItems: "center",
    gap: "0.375rem",
    padding: "0.3rem 0.75rem",
    borderRadius: 99,
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  badgeDot: {
    width: 7,
    height: 7,
    borderRadius: "50%",
    flexShrink: 0,
  },
  pct: {
    fontWeight: 800,
    fontSize: 15,
  },
  track: {
    height: 8,
    borderRadius: 99,
    overflow: "hidden",
  },
  fill: {
    height: "100%",
    borderRadius: 99,
  },
  hint: {
    margin: 0,
    fontSize: 12,
    color: "#94a3b8",
    fontStyle: "italic",
  },
  errorBox: {
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: 10,
    padding: "0.75rem 1rem",
  },
  errorLabel: {
    fontSize: 11,
    fontWeight: 700,
    color: "#ef4444",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: "0.25rem",
  },
  errorText: {
    fontSize: 13,
    color: "#b91c1c",
    lineHeight: 1.5,
    wordBreak: "break-word",
  },
  accordion: {
    borderTop: "1px solid #f1f5f9",
    marginTop: "0.125rem",
    paddingTop: "0.75rem",
  },
  accordionTrigger: {
    width: "100%",
    background: "none",
    border: "none",
    cursor: "pointer",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 0,
    color: "#64748b",
  },
  accordionLabel: {
    display: "flex",
    alignItems: "center",
    gap: "0.4rem",
    fontSize: 13,
    fontWeight: 600,
  },
  accordionIcon: { fontSize: 13, opacity: 0.7 },
  chevron: {
    fontSize: 14,
    transition: "transform 0.2s ease",
    display: "inline-block",
    color: "#94a3b8",
  },
  accordionBody: {
    overflow: "hidden",
    transition: "max-height 0.25s ease, opacity 0.2s ease",
  },
  accordionInner: {
    paddingTop: "0.75rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.375rem",
  },
  regexLabel: {
    fontSize: 11,
    fontWeight: 700,
    color: "#94a3b8",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  regexCode: {
    background: "#f8fafc",
    border: "1px solid #e2e8f0",
    padding: "0.5rem 0.75rem",
    borderRadius: 8,
    fontSize: 12.5,
    wordBreak: "break-all",
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
    color: "#4338ca",
    lineHeight: 1.6,
  },
};
