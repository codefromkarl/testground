import { useMemo } from 'react';
import type { ObsEvent } from '../types';

interface TimelineProps {
  events: ObsEvent[];
  selectedType: string;
  onEventClick: (event: ObsEvent) => void;
  selectedEventId?: string;
}

const TYPE_COLORS: Record<string, string> = {
  'test.pass': '#3fb950',
  'test.end': '#3fb950',
  'test.start': '#58a6ff',
  'test.fail': '#f85149',
  'assert.pass': '#3fb950',
  'assert.fail': '#f85149',
  'game': '#d2a8ff',
  'game.state_change': '#d2a8ff',
  'bench': '#e3b341',
  'agent.tool_call': '#bc8cff',
  'agent.tool_result': '#8b949e',
  'report.bug_candidate': '#f85149',
  'report.gate_result': '#58a6ff',
};

const TYPE_ICONS: Record<string, string> = {
  'test.start': '▶',
  'test.end': '✅',
  'test.fail': '❌',
  'assert.pass': '✓',
  'assert.fail': '✗',
  'agent.tool_call': '🔧',
  'game.state_change': '🎮',
  'report.bug_candidate': '🐛',
  'report.gate_result': '🚦',
};

function getEventColor(type: string): string {
  if (TYPE_COLORS[type]) return TYPE_COLORS[type];
  if (type.startsWith('test.')) return TYPE_COLORS['test.end'];
  if (type.startsWith('assert.')) return '#58a6ff';
  if (type.startsWith('game.')) return TYPE_COLORS['game'];
  if (type.startsWith('agent.')) return TYPE_COLORS['agent.tool_call'];
  if (type.startsWith('report.')) return '#58a6ff';
  return '#8b949e';
}

function getEventIcon(type: string): string {
  if (TYPE_ICONS[type]) return TYPE_ICONS[type];
  if (type.includes('fail')) return '✗';
  if (type.includes('pass')) return '✓';
  return '●';
}

function formatEventLabel(event: ObsEvent): string {
  const data = event.data || {};
  const name =
    (data.test_name as string) ||
    (data.assertion_name as string) ||
    (data.tool_name as string) ||
    (data.scene_path as string) ||
    (data.description as string)?.slice(0, 30) ||
    '';
  return name;
}

export default function Timeline({
  events,
  selectedType,
  onEventClick,
  selectedEventId,
}: TimelineProps) {
  const filtered = useMemo(() => {
    if (!selectedType) return events;
    return events.filter((e) => e.type === selectedType);
  }, [events, selectedType]);

  const timeRange = useMemo(() => {
    if (filtered.length === 0) return { min: 0, max: 1 };
    const timestamps = filtered.map((e) => e.timestamp);
    const min = Math.min(...timestamps);
    const max = Math.max(...timestamps);
    return { min, max: max === min ? min + 1 : max };
  }, [filtered]);

  if (filtered.length === 0) {
    return (
      <div style={styles.empty}>
        <span>暂无事件数据</span>
      </div>
    );
  }

  const range = timeRange.max - timeRange.min;

  return (
    <div style={styles.container}>
      <div style={styles.axis}>
        {/* Time labels */}
        <span style={styles.timeLabel}>
          {new Date(timeRange.min).toLocaleTimeString('zh-CN')}
        </span>
        <span style={styles.timeLabel}>
          {new Date(timeRange.max).toLocaleTimeString('zh-CN')}
        </span>
      </div>
      <div style={styles.track}>
        {filtered.map((event) => {
          const pct = ((event.timestamp - timeRange.min) / range) * 100;
          const color = getEventColor(event.type);
          const icon = getEventIcon(event.type);
          const label = formatEventLabel(event);
          const isSelected = event.event_id === selectedEventId;

          return (
            <div
              key={event.event_id}
              style={{
                ...styles.eventDot,
                left: `${pct}%`,
                borderColor: color,
                background: isSelected ? color : 'transparent',
              }}
              title={`[${event.type}] ${label}`}
              onClick={() => onEventClick(event)}
            >
              <span style={styles.eventIcon}>{icon}</span>
              {label && (
                <span style={{ ...styles.eventLabel, color }}>
                  {label.length > 12 ? label.slice(0, 12) + '…' : label}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {/* Timeline axis line */}
      <div style={styles.line} />
      <div style={styles.summary}>
        共 {filtered.length} 个事件
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'relative',
    padding: '40px 20px 20px',
    minHeight: 160,
  },
  axis: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 11,
    color: '#8b949e',
    marginBottom: 8,
  },
  timeLabel: {},
  track: {
    position: 'relative',
    height: 80,
    margin: '0 10px',
  },
  line: {
    height: 1,
    background: '#30363d',
    margin: '0 10px',
  },
  eventDot: {
    position: 'absolute',
    top: 10,
    transform: 'translateX(-50%)',
    border: '2px solid',
    borderRadius: 4,
    padding: '2px 6px',
    cursor: 'pointer',
    fontSize: 11,
    whiteSpace: 'nowrap',
    transition: 'background 0.15s',
    zIndex: 1,
  },
  eventIcon: {
    marginRight: 2,
  },
  eventLabel: {
    fontSize: 10,
  },
  empty: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: 120,
    color: '#8b949e',
    fontSize: 14,
  },
  summary: {
    fontSize: 11,
    color: '#8b949e',
    marginTop: 8,
    textAlign: 'center' as const,
  },
};
