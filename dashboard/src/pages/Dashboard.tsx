import { useEffect, useState } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import type { Session } from '../types';
import { useApi } from '../hooks/useApi';

export default function Dashboard() {
  const [recentSessions, setRecentSessions] = useState<Session[]>([]);
  const { data: sessionsData, loading: sessionsLoading } = useApi<{ sessions: Session[]; count: number }>('/sessions?limit=10');

  useEffect(() => {
    if (sessionsData?.sessions) {
      setRecentSessions(sessionsData.sessions);
    }
  }, [sessionsData]);

  // Compute stats
  const stats = (() => {
    const total = recentSessions.length;
    const passed = recentSessions.filter(
      (s) => s.gate_result?.verdict === 'PASS'
    ).length;
    const totalEvents = recentSessions.reduce(
      (sum, s) => sum + (s.total_events || 0),
      0
    );
    return { total, passed, totalEvents };
  })();

  const passRate = stats.total > 0 ? Math.round((stats.passed / stats.total) * 100) : 0;

  const gaugeData = [
    { name: 'passed', value: passRate },
    { name: 'remaining', value: 100 - passRate },
  ];

  const gaugeColor = passRate >= 80 ? '#3fb950' : passRate >= 50 ? '#e3b341' : '#f85149';

  return (
    <div style={styles.page}>
      <h2 style={styles.pageTitle}>仪表盘</h2>

      {/* Overview cards */}
      <div style={styles.cards}>
        <div style={styles.card}>
          <div style={styles.cardLabel}>会话总数</div>
          <div style={styles.cardValue}>{stats.total}</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardLabel}>通过率</div>
          <div style={{ ...styles.cardValue, color: gaugeColor }}>{passRate}%</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardLabel}>事件总数</div>
          <div style={styles.cardValue}>{stats.totalEvents}</div>
        </div>
        <div style={styles.card}>
          <div style={styles.cardLabel}>通过数</div>
          <div style={{ ...styles.cardValue, color: '#3fb950' }}>{stats.passed}</div>
        </div>
      </div>

      {/* Gauge chart */}
      <div style={styles.gaugeSection}>
        <h3 style={styles.sectionTitle}>质量评分</h3>
        <div style={styles.gaugeContainer}>
          <ResponsiveContainer width={200} height={200}>
            <PieChart>
              <Pie
                data={gaugeData}
                dataKey="value"
                startAngle={180}
                endAngle={0}
                innerRadius={60}
                outerRadius={80}
                stroke="none"
              >
                <Cell fill={gaugeColor} />
                <Cell fill="#21262d" />
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div style={styles.gaugeLabel}>
            <span style={{ ...styles.gaugeValue, color: gaugeColor }}>{passRate}</span>
            <span style={styles.gaugeUnit}>/ 100</span>
          </div>
        </div>
      </div>

      {/* Recent sessions */}
      <div style={styles.recentSection}>
        <h3 style={styles.sectionTitle}>最近会话</h3>
        {sessionsLoading ? (
          <div style={styles.loading}>加载中...</div>
        ) : (
          <div style={styles.table}>
            {recentSessions.map((session) => (
              <SessionRow key={session.session_id} session={session} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SessionRow({ session }: { session: Session }) {
  const project = session.project || 'unknown';
  const status = session.gate_result?.verdict || 'PENDING';
  const statusColor =
    status === 'PASS'
      ? '#3fb950'
      : status === 'FAIL'
        ? '#f85149'
        : '#e3b341';
  const date = new Date(session.started_at).toLocaleString('zh-CN');

  return (
    <a href={`#/sessions/${session.session_id}`} style={styles.row}>
      <span style={styles.rowProject}>{project}</span>
      <span style={styles.rowId}>{session.session_id.slice(0, 16)}…</span>
      <span style={{ ...styles.rowStatus, color: statusColor }}>{status}</span>
      <span style={styles.rowEvents}>{session.total_events || 0} 事件</span>
      <span style={styles.rowTime}>{date}</span>
    </a>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: { padding: 24 },
  pageTitle: { fontSize: 20, color: '#c9d1d9', marginBottom: 20, fontWeight: 600 },
  cards: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: 16,
    marginBottom: 32,
  },
  card: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    padding: 20,
  },
  cardLabel: { fontSize: 12, color: '#8b949e', marginBottom: 8 },
  cardValue: { fontSize: 28, fontWeight: 700, color: '#c9d1d9' },
  gaugeSection: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    padding: 20,
    marginBottom: 32,
  },
  sectionTitle: { fontSize: 14, color: '#c9d1d9', marginBottom: 16, fontWeight: 600 },
  gaugeContainer: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
  },
  gaugeLabel: {
    position: 'absolute',
    display: 'flex',
    alignItems: 'baseline',
    gap: 4,
  },
  gaugeValue: { fontSize: 32, fontWeight: 700 },
  gaugeUnit: { fontSize: 14, color: '#8b949e' },
  recentSection: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    padding: 20,
  },
  loading: { color: '#8b949e', textAlign: 'center' as const, padding: 20 },
  table: {},
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    padding: '10px 12px',
    borderBottom: '1px solid #21262d',
    textDecoration: 'none',
    color: 'inherit',
    transition: 'background 0.15s',
  },
  rowProject: {
    fontSize: 13,
    fontWeight: 600,
    color: '#58a6ff',
    minWidth: 120,
  },
  rowId: { fontSize: 12, color: '#8b949e', flex: 1 },
  rowStatus: { fontSize: 12, fontWeight: 600, minWidth: 60 },
  rowEvents: { fontSize: 12, color: '#8b949e', minWidth: 70 },
  rowTime: { fontSize: 11, color: '#8b949e' },
};
