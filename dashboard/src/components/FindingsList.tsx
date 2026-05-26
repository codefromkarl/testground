import { useMemo, useState } from 'react';
import type { Finding } from '../types';

interface FindingsListProps {
  findings: Finding[];
}

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#f85149',
  high: '#f0883e',
  medium: '#e3b341',
  low: '#3fb950',
};

const SEVERITY_BG: Record<string, string> = {
  critical: '#f8514920',
  high: '#f0883e20',
  medium: '#e3b34120',
  low: '#3fb95020',
};

export default function FindingsList({ findings }: FindingsListProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const sorted = useMemo(() => {
    return [...findings].sort((a, b) => {
      const orderA = SEVERITY_ORDER[a.severity] ?? 4;
      const orderB = SEVERITY_ORDER[b.severity] ?? 4;
      return orderA - orderB;
    });
  }, [findings]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (findings.length === 0) {
    return <div style={styles.empty}>暂无 Findings</div>;
  }

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.headerTitle}>Findings</span>
        <span style={styles.headerCount}>{findings.length} 项</span>
      </div>
      {sorted.map((finding, i) => {
        const id = finding.id || `finding-${i}`;
        const isExpanded = expanded.has(id);
        const color = SEVERITY_COLORS[finding.severity] || '#8b949e';
        const bg = SEVERITY_BG[finding.severity] || '#8b949e20';

        return (
          <div
            key={id}
            style={{ ...styles.item, borderLeftColor: color }}
            onClick={() => toggleExpand(id)}
          >
            <div style={styles.itemHeader}>
              <span
                style={{
                  ...styles.severityBadge,
                  color,
                  background: bg,
                }}
              >
                {finding.severity.toUpperCase()}
              </span>
              <span style={styles.category}>{finding.category}</span>
              <span style={styles.title}>{finding.title}</span>
              <span style={styles.expandIcon}>{isExpanded ? '▼' : '▶'}</span>
            </div>
            {isExpanded && (
              <div style={styles.detail}>
                <p style={styles.description}>{finding.description}</p>
                {finding.evidence && (
                  <div style={styles.section}>
                    <span style={styles.sectionTitle}>Evidence</span>
                    <pre style={styles.pre}>{finding.evidence}</pre>
                  </div>
                )}
                {finding.suggested_fix && (
                  <div style={styles.section}>
                    <span style={styles.sectionTitle}>Suggested Fix</span>
                    <pre style={styles.pre}>{finding.suggested_fix}</pre>
                  </div>
                )}
                {finding.confidence != null && (
                  <span style={styles.confidence}>
                    置信度: {(finding.confidence * 100).toFixed(0)}%
                  </span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {},
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: '#c9d1d9',
  },
  headerCount: {
    fontSize: 12,
    color: '#8b949e',
  },
  empty: {
    color: '#8b949e',
    padding: 20,
    textAlign: 'center' as const,
  },
  item: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderLeft: '3px solid',
    borderRadius: 6,
    marginBottom: 8,
    padding: '10px 12px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  itemHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  severityBadge: {
    padding: '2px 8px',
    borderRadius: 12,
    fontSize: 10,
    fontWeight: 600,
  },
  category: {
    fontSize: 11,
    color: '#8b949e',
  },
  title: {
    flex: 1,
    fontSize: 13,
    color: '#c9d1d9',
  },
  expandIcon: {
    fontSize: 10,
    color: '#8b949e',
  },
  detail: {
    marginTop: 10,
    paddingTop: 10,
    borderTop: '1px solid #21262d',
  },
  description: {
    fontSize: 12,
    color: '#8b949e',
    lineHeight: 1.6,
    margin: 0,
  },
  section: {
    marginTop: 10,
  },
  sectionTitle: {
    fontSize: 11,
    color: '#58a6ff',
    fontWeight: 600,
    display: 'block',
    marginBottom: 4,
  },
  pre: {
    background: '#0d1117',
    padding: 8,
    borderRadius: 4,
    fontSize: 11,
    color: '#c9d1d9',
    overflow: 'auto',
    margin: 0,
    lineHeight: 1.5,
  },
  confidence: {
    fontSize: 11,
    color: '#8b949e',
    marginTop: 8,
    display: 'block',
  },
};
