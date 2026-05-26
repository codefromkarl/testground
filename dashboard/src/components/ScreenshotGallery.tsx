import { useState } from 'react';
import type { Screenshot } from '../types';

interface ScreenshotGalleryProps {
  screenshots: Screenshot[];
  loading: boolean;
}

export default function ScreenshotGallery({ screenshots, loading }: ScreenshotGalleryProps) {
  const [selected, setSelected] = useState<Screenshot | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareSelection, setCompareSelection] = useState<Screenshot[]>([]);

  const toggleCompare = (s: Screenshot) => {
    setCompareSelection((prev) => {
      const exists = prev.find((x) => x.screenshot_id === s.screenshot_id);
      if (exists) return prev.filter((x) => x.screenshot_id !== s.screenshot_id);
      if (prev.length >= 2) return [prev[1], s];
      return [...prev, s];
    });
  };

  if (loading) {
    return <div style={styles.loading}>加载截图中...</div>;
  }

  if (screenshots.length === 0) {
    return <div style={styles.empty}>暂无截图</div>;
  }

  return (
    <div>
      {/* Toolbar */}
      <div style={styles.toolbar}>
        <span style={styles.count}>{screenshots.length} 张截图</span>
        <button
          style={{
            ...styles.button,
            ...(compareMode ? styles.buttonActive : {}),
          }}
          onClick={() => {
            setCompareMode(!compareMode);
            setCompareSelection([]);
          }}
        >
          {compareMode ? '退出对比' : '对比模式'}
        </button>
      </div>

      {/* Compare view */}
      {compareMode && compareSelection.length === 2 && (
        <div style={styles.compareView}>
          {compareSelection.map((s) => (
            <div key={s.screenshot_id} style={styles.compareItem}>
              <img
                src={`data:image/png;base64,${s.base64_data}`}
                alt={s.filename || s.screenshot_id}
                style={styles.compareImage}
              />
              <div style={styles.compareLabel}>
                {s.filename || s.context || s.screenshot_id.slice(0, 8)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Grid */}
      <div style={styles.grid}>
        {screenshots.map((s) => {
          const isSelected = compareSelection.find(
            (x) => x.screenshot_id === s.screenshot_id
          );
          return (
            <div
              key={s.screenshot_id}
              style={{
                ...styles.card,
                ...(isSelected ? styles.cardSelected : {}),
              }}
              onClick={() => {
                if (compareMode) {
                  toggleCompare(s);
                } else {
                  setSelected(s);
                }
              }}
            >
              {s.base64_data ? (
                <img
                  src={`data:image/png;base64,${s.base64_data}`}
                  alt={s.filename || s.screenshot_id}
                  style={styles.thumbnail}
                />
              ) : (
                <div style={styles.placeholder}>📷</div>
              )}
              <div style={styles.cardInfo}>
                <span style={styles.cardName}>
                  {s.filename || s.context || s.screenshot_id.slice(0, 8)}
                </span>
                <span style={styles.cardTime}>
                  {new Date(s.timestamp).toLocaleTimeString('zh-CN')}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Fullscreen overlay */}
      {selected && !compareMode && (
        <div style={styles.overlay} onClick={() => setSelected(null)}>
          <div style={styles.overlayContent} onClick={(e) => e.stopPropagation()}>
            <img
              src={`data:image/png;base64,${selected.base64_data}`}
              alt={selected.filename || ''}
              style={styles.fullImage}
            />
            <div style={styles.overlayInfo}>
              <span>{selected.filename || selected.screenshot_id}</span>
              <span>{selected.context}</span>
              <span>{new Date(selected.timestamp).toLocaleString('zh-CN')}</span>
            </div>
            <button style={styles.closeBtn} onClick={() => setSelected(null)}>
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  loading: { color: '#8b949e', padding: 20, textAlign: 'center' as const },
  empty: { color: '#8b949e', padding: 20, textAlign: 'center' as const },
  toolbar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  count: { fontSize: 13, color: '#8b949e' },
  button: {
    background: '#21262d',
    border: '1px solid #30363d',
    color: '#c9d1d9',
    padding: '6px 12px',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: 12,
  },
  buttonActive: {
    background: '#1f6feb',
    borderColor: '#1f6feb',
    color: '#fff',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
    gap: 12,
  },
  card: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 8,
    overflow: 'hidden',
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
  cardSelected: {
    borderColor: '#58a6ff',
    boxShadow: '0 0 0 2px #1f6feb33',
  },
  thumbnail: {
    width: '100%',
    height: 120,
    objectFit: 'cover' as const,
    display: 'block',
  },
  placeholder: {
    width: '100%',
    height: 120,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 32,
    background: '#0d1117',
  },
  cardInfo: {
    padding: '6px 8px',
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 11,
  },
  cardName: { color: '#c9d1d9' },
  cardTime: { color: '#8b949e' },
  overlay: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: 'rgba(0,0,0,0.8)',
    zIndex: 200,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  overlayContent: {
    position: 'relative',
    maxWidth: '90vw',
    maxHeight: '90vh',
  },
  fullImage: {
    maxWidth: '90vw',
    maxHeight: '80vh',
    borderRadius: 8,
  },
  overlayInfo: {
    display: 'flex',
    gap: 16,
    justifyContent: 'center',
    fontSize: 12,
    color: '#8b949e',
    marginTop: 8,
  },
  closeBtn: {
    position: 'absolute',
    top: -10,
    right: -10,
    background: '#21262d',
    border: '1px solid #30363d',
    color: '#c9d1d9',
    width: 28,
    height: 28,
    borderRadius: '50%',
    cursor: 'pointer',
    fontSize: 14,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  compareView: {
    display: 'flex',
    gap: 12,
    marginBottom: 16,
    background: '#0d1117',
    borderRadius: 8,
    padding: 12,
  },
  compareItem: {
    flex: 1,
    textAlign: 'center' as const,
  },
  compareImage: {
    maxWidth: '100%',
    maxHeight: 300,
    borderRadius: 4,
  },
  compareLabel: {
    fontSize: 11,
    color: '#8b949e',
    marginTop: 4,
  },
};
