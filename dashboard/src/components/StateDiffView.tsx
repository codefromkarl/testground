import type { StateTimelineEntry } from '../types';

interface StateDiffViewProps {
  timeline: StateTimelineEntry[];
  loading: boolean;
}

export default function StateDiffView({ timeline, loading }: StateDiffViewProps) {
  if (loading) {
    return <div style={styles.loading}>加载状态数据...</div>;
  }

  if (timeline.length === 0) {
    return <div style={styles.empty}>暂无状态数据</div>;
  }

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.title}>状态变化时间线</span>
        <span style={styles.count}>{timeline.length} 个快照</span>
      </div>
      {timeline.map((entry) => (
        <div key={entry.snapshot_id} style={styles.entry}>
          <div style={styles.entryHeader}>
            <span style={styles.entryIndex}>#{entry.index}</span>
            <span style={styles.entryTime}>
              {new Date(entry.timestamp).toLocaleTimeString('zh-CN')}
            </span>
          </div>
          <div style={styles.changes}>
            <DiffTree
              changes={entry.changes}
              prefix=""
            />
            {!entry.changes?.added &&
              !entry.changes?.removed &&
              !entry.changes?.modified && (
                <span style={styles.noChange}>无变化</span>
              )}
          </div>
        </div>
      ))}
    </div>
  );
}

function DiffTree({
  changes,
  prefix,
}: {
  changes: NonNullable<StateTimelineEntry['changes']>;
  prefix: string;
}) {
  const elements: React.ReactNode[] = [];

  // Added fields (green)
  if (changes.added && typeof changes.added === 'object') {
    for (const [key, value] of Object.entries(changes.added)) {
      const path = prefix ? `${prefix}.${key}` : key;
      elements.push(
        <div key={`add-${path}`} style={styles.line}>
          <span style={styles.added}>+ {path}</span>
          <span style={styles.value}>{formatValue(value)}</span>
        </div>
      );
    }
  }

  // Removed fields (red)
  if (changes.removed && typeof changes.removed === 'object') {
    for (const [key, value] of Object.entries(changes.removed)) {
      const path = prefix ? `${prefix}.${key}` : key;
      elements.push(
        <div key={`rm-${path}`} style={styles.line}>
          <span style={styles.removed}>- {path}</span>
          <span style={styles.value}>{formatValue(value)}</span>
        </div>
      );
    }
  }

  // Modified fields (yellow)
  if (changes.modified && typeof changes.modified === 'object') {
    for (const [key, value] of Object.entries(changes.modified)) {
      const path = prefix ? `${prefix}.${key}` : key;
      const mod = value as { from: unknown; to: unknown };
      elements.push(
        <div key={`mod-${path}`} style={styles.line}>
          <span style={styles.modified}>~ {path}</span>
          <span style={styles.valueOld}>{formatValue(mod.from)}</span>
          <span style={styles.arrow}>→</span>
          <span style={styles.valueNew}>{formatValue(mod.to)}</span>
        </div>
      );
    }
  }

  return <>{elements}</>;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

const styles: Record<string, React.CSSProperties> = {
  container: {},
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  title: { fontSize: 14, fontWeight: 600, color: '#c9d1d9' },
  count: { fontSize: 12, color: '#8b949e' },
  loading: { color: '#8b949e', padding: 20, textAlign: 'center' as const },
  empty: { color: '#8b949e', padding: 20, textAlign: 'center' as const },
  entry: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 6,
    marginBottom: 8,
    padding: '10px 12px',
  },
  entryHeader: {
    display: 'flex',
    gap: 12,
    marginBottom: 8,
  },
  entryIndex: {
    fontSize: 12,
    fontWeight: 600,
    color: '#58a6ff',
  },
  entryTime: {
    fontSize: 11,
    color: '#8b949e',
  },
  changes: {
    paddingLeft: 8,
  },
  line: {
    display: 'flex',
    gap: 8,
    fontSize: 12,
    lineHeight: 1.8,
    fontFamily: 'monospace',
  },
  added: { color: '#3fb950' },
  removed: { color: '#f85149' },
  modified: { color: '#e3b341' },
  value: { color: '#8b949e' },
  valueOld: { color: '#f85149', textDecoration: 'line-through' },
  arrow: { color: '#8b949e' },
  valueNew: { color: '#3fb950' },
  noChange: { fontSize: 11, color: '#8b949e', fontStyle: 'italic' },
};
