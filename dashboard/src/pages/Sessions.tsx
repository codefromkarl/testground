import { useMemo, useState } from 'react';
import type { Session } from '../types';
import { useApi } from '../hooks/useApi';

export default function Sessions() {
  const [search, setSearch] = useState('');
  const [projectFilter, setProjectFilter] = useState('');

  const params = new URLSearchParams();
  params.set('limit', '100');
  if (projectFilter) params.set('project', projectFilter);

  const { data, loading, error } = useApi<{ sessions: Session[]; count: number }>(
    `/sessions?${params.toString()}`
  );

  const sessions = data?.sessions || [];

  const filtered = useMemo(() => {
    if (!search) return sessions;
    const q = search.toLowerCase();
    return sessions.filter(
      (s) =>
        s.session_id.toLowerCase().includes(q) ||
        (s.project || '').toLowerCase().includes(q) ||
        (s.framework || '').toLowerCase().includes(q)
    );
  }, [sessions, search]);

  // Extract unique projects
  const projects = useMemo(() => {
    const set = new Set(sessions.map((s) => s.project).filter(Boolean));
    return Array.from(set) as string[];
  }, [sessions]);

  return (
    <div style={styles.page}>
      <h2 style={styles.title}>测试会话</h2>

      {/* Filters */}
      <div style={styles.filters}>
        <input
          style={styles.search}
          type="text"
          placeholder="搜索会话 ID、项目、框架..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          style={styles.select}
          value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value)}
        >
          <option value="">所有项目</option>
          {projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>

      {/* Results count */}
      <div style={styles.count}>
        {loading ? '加载中...' : `${filtered.length} 个会话`}
        {error && <span style={styles.error}> — 错误: {error}</span>}
      </div>

      {/* Table */}
      <div style={styles.table}>
        <div style={styles.tableHeader}>
          <span style={{ ...styles.col, ...styles.colProject }}>项目</span>
          <span style={{ ...styles.col, ...styles.colId }}>会话 ID</span>
          <span style={{ ...styles.col, ...styles.colFramework }}>框架</span>
          <span style={{ ...styles.col, ...styles.colStatus }}>状态</span>
          <span style={{ ...styles.col, ...styles.colEvents }}>事件数</span>
          <span style={{ ...styles.col, ...styles.colTime }}>时间</span>
        </div>
        {filtered.map((session) => (
          <SessionRow key={session.session_id} session={session} />
        ))}
      </div>
    </div>
  );
}

function SessionRow({ session }: { session: Session }) {
  const status = session.gate_result?.verdict || 'PENDING';
  const statusColor =
    status === 'PASS'
      ? '#3fb950'
      : status === 'FAIL'
        ? '#f85149'
        : '#e3b341';

  return (
    <a
      href={`#/sessions/${session.session_id}`}
      style={styles.tableRow}
    >
      <span style={{ ...styles.col, ...styles.colProject, color: '#58a6ff' }}>
        {session.project || '-'}
      </span>
      <span style={{ ...styles.col, ...styles.colId }} title={session.session_id}>
        {session.session_id.slice(0, 16)}…
      </span>
      <span style={{ ...styles.col, ...styles.colFramework }}>
        {session.framework || '-'}
      </span>
      <span style={{ ...styles.col, ...styles.colStatus, color: statusColor }}>
        {status}
      </span>
      <span style={{ ...styles.col, ...styles.colEvents }}>
        {session.total_events ?? '-'}
      </span>
      <span style={{ ...styles.col, ...styles.colTime }}>
        {new Date(session.started_at).toLocaleString('zh-CN')}
      </span>
    </a>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: { padding: 24 },
  title: { fontSize: 20, color: '#c9d1d9', marginBottom: 20, fontWeight: 600 },
  filters: {
    display: 'flex',
    gap: 12,
    marginBottom: 16,
  },
  search: {
    flex: 1,
    background: '#21262d',
    border: '1px solid #30363d',
    color: '#c9d1d9',
    padding: '8px 14px',
    borderRadius: 6,
    fontSize: 13,
    outline: 'none',
  },
  select: {
    background: '#21262d',
    border: '1px solid #30363d',
    color: '#c9d1d9',
    padding: '8px 14px',
    borderRadius: 6,
    fontSize: 13,
    minWidth: 140,
  },
  count: {
    fontSize: 12,
    color: '#8b949e',
    marginBottom: 12,
  },
  error: { color: '#f85149' },
  table: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    overflow: 'hidden',
  },
  tableHeader: {
    display: 'flex',
    alignItems: 'center',
    padding: '10px 16px',
    borderBottom: '1px solid #30363d',
    fontSize: 11,
    color: '#8b949e',
    fontWeight: 600,
    textTransform: 'uppercase' as const,
  },
  tableRow: {
    display: 'flex',
    alignItems: 'center',
    padding: '10px 16px',
    borderBottom: '1px solid #21262d',
    textDecoration: 'none',
    color: 'inherit',
    transition: 'background 0.15s',
  },
  col: { fontSize: 13 },
  colProject: { minWidth: 120 },
  colId: { flex: 1, color: '#8b949e' },
  colFramework: { minWidth: 80, color: '#8b949e' },
  colStatus: { minWidth: 70, fontWeight: 600 },
  colEvents: { minWidth: 70, color: '#8b949e' },
  colTime: { minWidth: 140, color: '#8b949e', fontSize: 12 },
};
