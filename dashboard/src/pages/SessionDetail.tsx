import { useEffect, useState } from 'react';
import type { Session, ObsEvent, AnalysisListResponse, Screenshot, StateTimelineEntry } from '../types';
import Timeline from '../components/Timeline';
import ScreenshotGallery from '../components/ScreenshotGallery';
import FindingsList from '../components/FindingsList';
import StateDiffView from '../components/StateDiffView';
import { useApi } from '../hooks/useApi';
import { useWebSocket } from '../hooks/useWebSocket';

type Tab = 'timeline' | 'screenshots' | 'analysis' | 'statediff' | 'report';

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'timeline', label: '时间线', icon: '📅' },
  { key: 'screenshots', label: '截图', icon: '📷' },
  { key: 'analysis', label: '分析', icon: '🔬' },
  { key: 'statediff', label: '状态', icon: '📊' },
  { key: 'report', label: '报告', icon: '📋' },
];

export default function SessionDetail() {
  const sessionId = window.location.hash.split('/sessions/')[1]?.split('/')[0] || '';

  const [activeTab, setActiveTab] = useState<Tab>('timeline');
  const [selectedEvent, setSelectedEvent] = useState<ObsEvent | null>(null);
  const [typeFilter, setTypeFilter] = useState('');

  // Session data
  const { data: session, loading: sessionLoading } = useApi<Session>(
    sessionId ? `/sessions/${sessionId}` : null
  );

  // Timeline events
  const { data: timelineData, loading: timelineLoading } = useApi<{
    events: ObsEvent[];
    count: number;
  }>(
    sessionId ? `/sessions/${sessionId}/timeline?limit=5000` : null
  );

  // Analysis
  const { data: analysisData } = useApi<AnalysisListResponse>(
    sessionId ? `/sessions/${sessionId}/analysis` : null
  );

  // Screenshots
  const { data: screenshotData, loading: screenshotLoading } = useApi<{
    screenshots: Screenshot[];
    count: number;
  }>(sessionId ? `/sessions/${sessionId}/screenshots?limit=100` : null);

  // State timeline
  const { data: stateData, loading: stateLoading } = useApi<{
    timeline: StateTimelineEntry[];
    count: number;
  }>(sessionId ? `/sessions/${sessionId}/states/timeline` : null);

  // Report
  const [reportHtml, setReportHtml] = useState('');
  useEffect(() => {
    if (activeTab === 'report' && sessionId) {
      fetch(`/api/sessions/${sessionId}/report?format=html`)
        .then((r) => r.text())
        .then(setReportHtml)
        .catch(() => setReportHtml('<p style="color:#f85149">报告加载失败</p>'));
    }
  }, [activeTab, sessionId]);

  // WebSocket
  const { connected: wsConnected, events: wsEvents } = useWebSocket({
    sessionId,
  });

  // Merge WS events with fetched events
  const allEvents = [...(timelineData?.events || []), ...wsEvents];

  // Gate result display
  const gateResult = session?.gate_result;
  const gateColor =
    gateResult?.verdict === 'PASS'
      ? '#3fb950'
      : gateResult?.verdict === 'FAIL'
        ? '#f85149'
        : '#e3b341';

  if (!sessionId) {
    return <div style={styles.error}>未指定会话 ID</div>;
  }

  return (
    <div style={styles.page}>
      {/* Header */}
      <div style={styles.header}>
        <a href="#/" style={styles.backLink}>← 返回</a>
        <h2 style={styles.title}>
          会话详情
          {wsConnected && <span style={styles.wsBadge}>🟢 LIVE</span>}
        </h2>
      </div>

      {/* Session meta */}
      {sessionLoading ? (
        <div style={styles.loading}>加载会话信息...</div>
      ) : session ? (
        <div style={styles.meta}>
          <div style={styles.metaRow}>
            <span style={styles.metaLabel}>项目</span>
            <span style={styles.metaValue}>{session.project || '-'}</span>
          </div>
          <div style={styles.metaRow}>
            <span style={styles.metaLabel}>框架</span>
            <span style={styles.metaValue}>{session.framework || '-'}</span>
          </div>
          <div style={styles.metaRow}>
            <span style={styles.metaLabel}>事件数</span>
            <span style={styles.metaValue}>{session.total_events || 0}</span>
          </div>
          <div style={styles.metaRow}>
            <span style={styles.metaLabel}>门禁</span>
            <span style={{ ...styles.metaValue, color: gateColor, fontWeight: 600 }}>
              {gateResult?.verdict || 'PENDING'}
            </span>
          </div>
          <div style={styles.metaRow}>
            <span style={styles.metaLabel}>开始时间</span>
            <span style={styles.metaValue}>
              {new Date(session.started_at).toLocaleString('zh-CN')}
            </span>
          </div>
          {session.duration_ms && (
            <div style={styles.metaRow}>
              <span style={styles.metaLabel}>持续时间</span>
              <span style={styles.metaValue}>{session.duration_ms}ms</span>
            </div>
          )}
        </div>
      ) : null}

      {/* Tabs */}
      <div style={styles.tabs}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            style={{
              ...styles.tab,
              ...(activeTab === tab.key ? styles.tabActive : {}),
            }}
            onClick={() => setActiveTab(tab.key)}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={styles.content}>
        {activeTab === 'timeline' && (
          <div>
            <div style={styles.timelineControls}>
              <select
                style={styles.typeSelect}
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="">所有类型</option>
                <option value="test.start">test.start</option>
                <option value="test.end">test.end</option>
                <option value="test.fail">test.fail</option>
                <option value="assert.pass">assert.pass</option>
                <option value="assert.fail">assert.fail</option>
                <option value="agent.tool_call">agent.tool_call</option>
                <option value="game.state_change">game.state_change</option>
                <option value="report.bug_candidate">report.bug_candidate</option>
              </select>
              <span style={styles.eventCount}>
                {timelineLoading ? '加载中...' : `${allEvents.length} 个事件`}
              </span>
            </div>
            <Timeline
              events={allEvents}
              selectedType={typeFilter}
              onEventClick={setSelectedEvent}
              selectedEventId={selectedEvent?.event_id}
            />
            {/* Event detail panel */}
            {selectedEvent && (
              <div style={styles.detailPanel}>
                <div style={styles.detailHeader}>
                  <h3 style={styles.detailTitle}>事件详情</h3>
                  <button style={styles.closeBtn} onClick={() => setSelectedEvent(null)}>✕</button>
                </div>
                <div style={styles.detailBody}>
                  <p style={styles.detailField}>
                    <strong>事件 ID:</strong> {selectedEvent.event_id}
                  </p>
                  <p style={styles.detailField}>
                    <strong>类型:</strong>{' '}
                    <span style={styles.typeBadge}>{selectedEvent.type}</span>
                  </p>
                  <p style={styles.detailField}>
                    <strong>时间:</strong>{' '}
                    {new Date(selectedEvent.timestamp).toLocaleString('zh-CN')}
                  </p>
                  {selectedEvent.source?.project && (
                    <p style={styles.detailField}>
                      <strong>来源:</strong> {selectedEvent.source.project}
                    </p>
                  )}
                  <h4 style={styles.detailSubtitle}>数据</h4>
                  <pre style={styles.pre}>
                    {JSON.stringify(selectedEvent.data, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}

        {activeTab === 'screenshots' && (
          <ScreenshotGallery
            screenshots={screenshotData?.screenshots || []}
            loading={screenshotLoading}
          />
        )}

        {activeTab === 'analysis' && (
          <div>
            {(analysisData?.analyses || []).flatMap((a) => a.findings).length > 0 ? (
              <FindingsList
                findings={(analysisData?.analyses || []).flatMap((a) => a.findings)}
              />
            ) : (
              <div style={styles.empty}>暂无分析结果</div>
            )}
            {(analysisData?.analyses || []).map((a) => (
              <div key={a.analysis_id} style={styles.analysisCard}>
                <div style={styles.analysisHeader}>
                  <span style={styles.analysisAnalyzer}>{a.analyzer}</span>
                  <span style={styles.analysisConfidence}>
                    置信度: {(a.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                <p style={styles.analysisSummary}>{a.summary}</p>
                {a.recommendations?.length && (
                  <div style={styles.recommendations}>
                    <strong>建议:</strong>
                    <ul>
                      {a.recommendations.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {activeTab === 'statediff' && (
          <StateDiffView
            timeline={stateData?.timeline || []}
            loading={stateLoading}
          />
        )}

        {activeTab === 'report' && (
          <div style={styles.reportContainer}>
            {reportHtml ? (
              <div
                style={styles.reportContent}
                dangerouslySetInnerHTML={{ __html: reportHtml }}
              />
            ) : (
              <div style={styles.loading}>加载报告中...</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: { padding: 24 },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    marginBottom: 16,
  },
  backLink: {
    color: '#58a6ff',
    textDecoration: 'none',
    fontSize: 13,
  },
  title: { fontSize: 20, color: '#c9d1d9', fontWeight: 600, margin: 0 },
  wsBadge: {
    fontSize: 11,
    marginLeft: 8,
    color: '#3fb950',
  },
  loading: { color: '#8b949e', padding: 20, textAlign: 'center' as const },
  error: { color: '#f85149', padding: 20 },
  empty: { color: '#8b949e', padding: 20, textAlign: 'center' as const },
  meta: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '8px 24px',
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    padding: '14px 20px',
    marginBottom: 16,
  },
  metaRow: { display: 'flex', gap: 8, alignItems: 'center' },
  metaLabel: { fontSize: 12, color: '#8b949e' },
  metaValue: { fontSize: 13, color: '#c9d1d9' },
  tabs: {
    display: 'flex',
    gap: 4,
    marginBottom: 16,
    borderBottom: '1px solid #30363d',
    paddingBottom: 0,
  },
  tab: {
    background: 'none',
    border: 'none',
    color: '#8b949e',
    padding: '10px 16px',
    cursor: 'pointer',
    fontSize: 13,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    borderBottom: '2px solid transparent',
    transition: 'all 0.15s',
  },
  tabActive: {
    color: '#c9d1d9',
    borderBottomColor: '#58a6ff',
  },
  content: {
    minHeight: 400,
  },
  timelineControls: {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
    marginBottom: 12,
  },
  typeSelect: {
    background: '#21262d',
    border: '1px solid #30363d',
    color: '#c9d1d9',
    padding: '6px 12px',
    borderRadius: 6,
    fontSize: 12,
  },
  eventCount: { fontSize: 12, color: '#8b949e' },
  detailPanel: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    marginTop: 16,
  },
  detailHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 16px',
    borderBottom: '1px solid #30363d',
  },
  detailTitle: { fontSize: 14, color: '#58a6ff', margin: 0 },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: '#8b949e',
    cursor: 'pointer',
    fontSize: 16,
  },
  detailBody: { padding: 16 },
  detailField: { fontSize: 13, color: '#c9d1d9', margin: '4px 0' },
  detailSubtitle: { fontSize: 13, color: '#58a6ff', marginTop: 12, marginBottom: 8 },
  typeBadge: {
    display: 'inline-block',
    padding: '2px 8px',
    borderRadius: 12,
    fontSize: 11,
    background: '#1f6feb33',
    color: '#58a6ff',
  },
  pre: {
    background: '#0d1117',
    padding: 12,
    borderRadius: 6,
    overflow: 'auto',
    fontSize: 12,
    lineHeight: 1.5,
    color: '#c9d1d9',
  },
  analysisCard: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    padding: 16,
    marginTop: 12,
  },
  analysisHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  analysisAnalyzer: { fontSize: 13, color: '#58a6ff', fontWeight: 600 },
  analysisConfidence: { fontSize: 12, color: '#8b949e' },
  analysisSummary: { fontSize: 13, color: '#c9d1d9', lineHeight: 1.6, margin: 0 },
  recommendations: {
    marginTop: 12,
    fontSize: 12,
    color: '#8b949e',
    lineHeight: 1.6,
  },
  reportContainer: {
    background: '#fff',
    borderRadius: 8,
    padding: 24,
    minHeight: 400,
  },
  reportContent: {},
};
