import React, { useState } from "react";
import { cancelJob } from "../api/client";
import { PaginatedResultTable } from "../components/PaginatedResultTable";
import { ProgressBar } from "../components/ProgressBar";
import { UploadForm } from "../components/UploadForm";
import { useJobPolling } from "../hooks/useJobPolling";

export default function HomePage() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [targetColumn, setTargetColumn] = useState<string>("");
  const [cancelling, setCancelling] = useState(false);

  const { jobStatus, isPolling, error: pollError } = useJobPolling(jobId, {
    intervalMs: 2000,
  });

  const handleJobCreated = (id: string, column: string) => {
    setJobId(id);
    setTargetColumn(column);
  };

  const handleCancel = async () => {
    if (!jobId) return;
    setCancelling(true);
    try {
      await cancelJob(jobId);
    } catch {
      // ignore — status will update on the next poll
    } finally {
      setCancelling(false);
    }
  };

  const handleReset = () => {
    setJobId(null);
    setTargetColumn("");
  };

  const isTerminal =
    jobStatus?.status === "SUCCESS" ||
    jobStatus?.status === "FAILED" ||
    jobStatus?.status === "CANCELLED";

  return (
    <div style={styles.page}>
      {/* ── Top nav ── */}
      <header style={styles.nav}>
        <div style={styles.navInner}>
          <div style={styles.brand}>
            <span style={styles.brandMark} />
            <span style={styles.brandName}>Rhombus</span>
          </div>

          <div style={styles.navRight}>
            {/* Job ID chip — visible when a job is active */}
            {jobId && (
              <div style={styles.jobChip}>
                <span style={styles.jobChipLabel}>Job</span>
                <code style={styles.jobChipCode}>
                  {jobId.slice(0, 8)}…
                </code>
              </div>
            )}

            {/* "New Job" button — only shown when a job is loaded */}
            {jobId && isTerminal && (
              <button onClick={handleReset} style={styles.newJobBtn}>
                + New Job
              </button>
            )}

            {/* Cancel button — only during active run */}
            {jobId && isPolling && jobStatus?.status === "RUNNING" && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                style={{
                  ...styles.cancelBtn,
                  opacity: cancelling ? 0.6 : 1,
                  cursor: cancelling ? "not-allowed" : "pointer",
                }}
              >
                {cancelling ? "Cancelling…" : "Cancel Job"}
              </button>
            )}
          </div>
        </div>
      </header>

      {/* ── Hero tagline (only on the form view) ── */}
      {!jobId && (
        <div style={styles.hero}>
          <h1 style={styles.heroTitle}>
            Regex at{" "}
            <span style={styles.heroAccent}>scale</span>
          </h1>
          <p style={styles.heroSub}>
            Upload a CSV or Excel file, describe a pattern in plain English, and
            we'll generate a regex and apply it across millions of rows with PySpark.
          </p>
        </div>
      )}

      {/* ── Main content ── */}
      <main style={styles.main}>
        {!jobId ? (
          <UploadForm onJobCreated={handleJobCreated} />
        ) : (
          <div style={styles.jobLayout}>
            {/* Status card */}
            {jobStatus && (
              <ProgressBar
                status={jobStatus.status}
                progress={jobStatus.progress}
                generatedRegex={jobStatus.generated_regex || undefined}
                errorMessage={jobStatus.error_message || undefined}
              />
            )}

            {/* Transient poll error */}
            {pollError && (
              <div style={styles.pollError}>
                <span style={{ fontWeight: 700 }}>Polling error: </span>
                {pollError}
              </div>
            )}

            {/* Results table — only when SUCCESS */}
            {jobStatus?.status === "SUCCESS" && jobStatus.row_count !== null && (
              <PaginatedResultTable
                jobId={jobId}
                totalRows={jobStatus.row_count}
                targetColumn={targetColumn}
              />
            )}

            {/* Restart prompt after non-success terminal state */}
            {(jobStatus?.status === "FAILED" || jobStatus?.status === "CANCELLED") && (
              <div style={styles.terminalHint}>
                <span style={styles.terminalHintText}>
                  {jobStatus.status === "FAILED"
                    ? "This job failed. Review the error above, then start a new job."
                    : "This job was cancelled."}
                </span>
                <button onClick={handleReset} style={styles.newJobBtnInline}>
                  + New Job
                </button>
              </div>
            )}
          </div>
        )}
      </main>

      {/* ── Footer ── */}
      <footer style={styles.footer}>
        Rhombus · distributed NL-to-regex pipeline · Django + Celery + PySpark
      </footer>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    background: "#f1f5f9",
  },

  /* Nav */
  nav: {
    width: "100%",
    background: "#ffffff",
    borderBottom: "1px solid #e2e8f0",
    position: "sticky",
    top: 0,
    zIndex: 50,
    backdropFilter: "blur(8px)",
  },
  navInner: {
    maxWidth: 900,
    margin: "0 auto",
    padding: "0 1.5rem",
    height: 56,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  brand: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
  },
  brandMark: {
    display: "inline-block",
    width: 22,
    height: 22,
    background: "linear-gradient(135deg, #4f46e5, #7c3aed)",
    borderRadius: 6,
    transform: "rotate(45deg)",
    flexShrink: 0,
  },
  brandName: {
    fontSize: 17,
    fontWeight: 800,
    background: "linear-gradient(135deg, #4f46e5, #7c3aed)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
    letterSpacing: -0.3,
  },
  navRight: {
    display: "flex",
    alignItems: "center",
    gap: "0.625rem",
  },
  jobChip: {
    display: "flex",
    alignItems: "center",
    gap: "0.35rem",
    background: "#f8fafc",
    border: "1px solid #e2e8f0",
    borderRadius: 8,
    padding: "0.25rem 0.625rem",
  },
  jobChipLabel: {
    fontSize: 11,
    fontWeight: 700,
    color: "#94a3b8",
    textTransform: "uppercase",
    letterSpacing: 0.4,
  },
  jobChipCode: {
    fontSize: 12,
    fontFamily: "monospace",
    color: "#475569",
  },
  newJobBtn: {
    padding: "0.4rem 0.875rem",
    background: "#ffffff",
    color: "#4f46e5",
    border: "1.5px solid #6366f1",
    borderRadius: 8,
    fontWeight: 700,
    fontSize: 13,
    cursor: "pointer",
    transition: "background 0.12s",
    letterSpacing: 0.1,
  },
  cancelBtn: {
    padding: "0.4rem 0.875rem",
    background: "#fef2f2",
    color: "#dc2626",
    border: "1.5px solid #fca5a5",
    borderRadius: 8,
    fontWeight: 700,
    fontSize: 13,
    transition: "opacity 0.15s",
    letterSpacing: 0.1,
  },

  /* Hero */
  hero: {
    textAlign: "center",
    padding: "3.5rem 1.5rem 0",
    maxWidth: 520,
  },
  heroTitle: {
    fontSize: "2.5rem",
    fontWeight: 900,
    color: "#0f172a",
    letterSpacing: -1,
    marginBottom: "0.75rem",
    lineHeight: 1.1,
  },
  heroAccent: {
    background: "linear-gradient(135deg, #4f46e5, #7c3aed)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
  },
  heroSub: {
    color: "#64748b",
    fontSize: 15,
    lineHeight: 1.65,
    margin: 0,
  },

  /* Main */
  main: {
    width: "100%",
    maxWidth: 900,
    padding: "2rem 1.5rem",
    flex: 1,
    display: "flex",
    justifyContent: "center",
  },
  jobLayout: {
    width: "100%",
    display: "flex",
    flexDirection: "column",
    gap: "1.25rem",
  },
  pollError: {
    padding: "0.65rem 0.875rem",
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: 10,
    fontSize: 13,
    color: "#dc2626",
  },

  /* Terminal state hint */
  terminalHint: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    background: "#ffffff",
    border: "1px solid #e2e8f0",
    borderRadius: 12,
    padding: "0.875rem 1.25rem",
    gap: "1rem",
    flexWrap: "wrap",
  },
  terminalHintText: {
    fontSize: 13,
    color: "#64748b",
  },
  newJobBtnInline: {
    padding: "0.4rem 0.875rem",
    background: "#ffffff",
    color: "#4f46e5",
    border: "1.5px solid #6366f1",
    borderRadius: 8,
    fontWeight: 700,
    fontSize: 13,
    cursor: "pointer",
    whiteSpace: "nowrap",
  },

  /* Footer */
  footer: {
    padding: "1.5rem",
    fontSize: 12,
    color: "#94a3b8",
    textAlign: "center",
  },
};
