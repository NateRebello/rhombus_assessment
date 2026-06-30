import React, { useCallback, useEffect, useState } from "react";
import { ApiError, getJobResults, JobResultResponse } from "../api/client";

interface Props {
  jobId: string;
  totalRows: number;
  targetColumn?: string;
}

const PAGE_SIZE = 100;

export function PaginatedResultTable({ jobId, totalRows, targetColumn }: Props) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<JobResultResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchPage = useCallback(
    async (p: number) => {
      setLoading(true);
      setError(null);
      try {
        const result = await getJobResults(jobId, p, PAGE_SIZE);
        setData(result);
        setPage(p);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? `Failed to load page ${p}: ${err.message}`
            : "Unexpected error loading results."
        );
      } finally {
        setLoading(false);
      }
    },
    [jobId]
  );

  useEffect(() => {
    fetchPage(1);
  }, [fetchPage]);

  if (!data && loading) {
    return (
      <div style={styles.stateBox}>
        <div style={styles.loadingSpinner} />
        <span style={styles.stateText}>Loading results…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div style={styles.errorBox}>
        <span style={{ fontWeight: 700 }}>Error: </span>{error}
      </div>
    );
  }

  if (!data || Object.keys(data.results).length === 0) {
    return (
      <div style={styles.stateBox}>
        <span style={styles.stateText}>No results found.</span>
      </div>
    );
  }

  const columns = Object.keys(data.results);
  const rowCount = (data.results[columns[0]] as unknown[]).length;
  const canPrev = page > 1 && !loading;
  const canNext = page < data.total_pages && !loading;

  return (
    <div style={styles.wrapper}>
      {/* ── Table header: metadata + pagination ── */}
      <div style={styles.tableHeader}>
        <div style={styles.metaGroup}>
          <span style={styles.rowCount}>{totalRows.toLocaleString()}</span>
          <span style={styles.rowLabel}> total rows</span>
          <span style={styles.divider}>·</span>
          <span style={styles.pageIndicator}>
            page <strong>{data.page}</strong> of <strong>{data.total_pages}</strong>
          </span>
          {loading && <span style={styles.loadingDot} />}
        </div>

        <div style={styles.paginator}>
          <button
            style={{ ...styles.pageBtn, ...(canPrev ? {} : styles.pageBtnDisabled) }}
            onClick={() => fetchPage(page - 1)}
            disabled={!canPrev}
          >
            ← Prev
          </button>
          <button
            style={{ ...styles.pageBtn, ...(canNext ? {} : styles.pageBtnDisabled) }}
            onClick={() => fetchPage(page + 1)}
            disabled={!canNext}
          >
            Next →
          </button>
        </div>
      </div>

      {/* ── Data table ── */}
      <div style={styles.tableScroll}>
        <table style={styles.table}>
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col}
                  style={{
                    ...styles.th,
                    ...(col === targetColumn ? styles.thTarget : {}),
                  }}
                >
                  {col}
                  {col === targetColumn && (
                    <span style={styles.targetBadge}>processed</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: rowCount }).map((_, i) => (
              <tr key={i} style={i % 2 === 0 ? styles.rowEven : styles.rowOdd}>
                {columns.map((col) => {
                  const isTarget = col === targetColumn;
                  return (
                    <td
                      key={col}
                      style={{
                        ...styles.td,
                        ...(isTarget ? styles.tdTarget : {}),
                      }}
                    >
                      {isTarget ? (
                        <span style={styles.targetValue}>
                          {String((data.results[col] as unknown[])[i] ?? "")}
                        </span>
                      ) : (
                        String((data.results[col] as unknown[])[i] ?? "")
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Footer ── */}
      {data.total_pages > 1 && (
        <div style={styles.tableFooter}>
          <button
            style={{ ...styles.pageBtn, ...(canPrev ? {} : styles.pageBtnDisabled) }}
            onClick={() => fetchPage(page - 1)}
            disabled={!canPrev}
          >
            ← Prev
          </button>
          <span style={styles.footerPage}>
            {page} / {data.total_pages}
          </span>
          <button
            style={{ ...styles.pageBtn, ...(canNext ? {} : styles.pageBtnDisabled) }}
            onClick={() => fetchPage(page + 1)}
            disabled={!canNext}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    background: "#ffffff",
    borderRadius: 16,
    boxShadow: "0 1px 3px rgba(0,0,0,0.05), 0 4px 16px rgba(0,0,0,0.05)",
    border: "1px solid #e2e8f0",
    overflow: "hidden",
    width: "100%",
  },

  /* Header bar */
  tableHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "0.875rem 1.25rem",
    borderBottom: "1px solid #f1f5f9",
    background: "#fafcff",
    flexWrap: "wrap",
    gap: "0.5rem",
  },
  metaGroup: {
    display: "flex",
    alignItems: "center",
    gap: "0.3rem",
    fontSize: 13,
  },
  rowCount: { fontWeight: 700, color: "#0f172a", fontSize: 14 },
  rowLabel: { color: "#64748b" },
  divider: { color: "#cbd5e1", padding: "0 0.25rem" },
  pageIndicator: { color: "#64748b" },
  loadingDot: {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: "#f59e0b",
    marginLeft: "0.35rem",
    animation: "pulse 1.2s infinite",
  },

  /* Pagination buttons */
  paginator: { display: "flex", gap: "0.5rem" },
  pageBtn: {
    padding: "0.35rem 0.875rem",
    background: "#ffffff",
    border: "1.5px solid #e2e8f0",
    borderRadius: 8,
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 600,
    color: "#374151",
    transition: "background 0.12s, border-color 0.12s",
  },
  pageBtnDisabled: {
    opacity: 0.38,
    cursor: "not-allowed",
  },

  /* Table */
  tableScroll: { overflowX: "auto" },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 13,
  },
  th: {
    padding: "0.875rem 1rem",
    background: "#f8fafc",
    textAlign: "left",
    fontWeight: 700,
    fontSize: 12,
    letterSpacing: 0.3,
    color: "#475569",
    borderBottom: "1.5px solid #e2e8f0",
    whiteSpace: "nowrap",
    textTransform: "uppercase",
    position: "relative",
  },
  thTarget: {
    background: "#f0fdf4",
    color: "#065f46",
    borderBottom: "1.5px solid #bbf7d0",
  },
  targetBadge: {
    display: "inline-block",
    marginLeft: "0.45rem",
    fontSize: 9,
    fontWeight: 700,
    background: "#dcfce7",
    color: "#15803d",
    borderRadius: 4,
    padding: "0.1rem 0.35rem",
    letterSpacing: 0.3,
    verticalAlign: "middle",
    textTransform: "uppercase",
  },
  rowEven: { background: "#ffffff" },
  rowOdd: { background: "#f8fafc" },
  td: {
    padding: "0.8rem 1rem",
    borderBottom: "1px solid #f1f5f9",
    maxWidth: 320,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    color: "#374151",
    fontSize: 13,
  },
  tdTarget: {
    background: "rgba(16, 185, 129, 0.06)",
  },
  targetValue: {
    display: "inline-block",
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    fontSize: 12,
    color: "#065f46",
    background: "rgba(16,185,129,0.1)",
    borderRadius: 4,
    padding: "0.1rem 0.35rem",
  },

  /* Footer */
  tableFooter: {
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    gap: "0.875rem",
    padding: "0.875rem",
    borderTop: "1px solid #f1f5f9",
    background: "#fafcff",
  },
  footerPage: {
    fontSize: 13,
    fontWeight: 600,
    color: "#64748b",
    minWidth: 60,
    textAlign: "center",
  },

  /* State boxes */
  stateBox: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "0.75rem",
    padding: "3rem",
    background: "#fff",
    borderRadius: 16,
    border: "1px solid #e2e8f0",
  },
  stateText: { fontSize: 14, color: "#94a3b8" },
  loadingSpinner: {
    width: 18,
    height: 18,
    border: "2.5px solid #e2e8f0",
    borderTopColor: "#6366f1",
    borderRadius: "50%",
    animation: "spin 0.7s linear infinite",
    flexShrink: 0,
  },
  errorBox: {
    padding: "1rem 1.25rem",
    background: "#fef2f2",
    color: "#dc2626",
    border: "1px solid #fecaca",
    borderRadius: 12,
    fontSize: 13,
  },
};
