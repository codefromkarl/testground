import { HashRouter, Routes, Route, Link } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Sessions from './pages/Sessions';
import SessionDetail from './pages/SessionDetail';
import { useHealth } from './hooks/useApi';

function Header() {
  const { data: health } = useHealth();

  return (
    <header style={styles.header}>
      <div style={styles.headerLeft}>
        <Link to="/" style={styles.logo}>
          🔬 测试观测平台
        </Link>
        <nav style={styles.nav}>
          <Link to="/" style={styles.navLink}>
            仪表盘
          </Link>
          <Link to="/sessions" style={styles.navLink}>
            会话列表
          </Link>
        </nav>
      </div>
      <div style={styles.headerRight}>
        {health && (
          <span style={styles.health}>
            <span
              style={{
                ...styles.healthDot,
                background: health.status === 'ok' ? '#3fb950' : '#f85149',
              }}
            />
            Gateway v{health.version}
          </span>
        )}
      </div>
    </header>
  );
}

export default function App() {
  return (
    <HashRouter>
      <div style={styles.app}>
        <Header />
        <main style={styles.main}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/:sessionId" element={<SessionDetail />} />
          </Routes>
        </main>
      </div>
    </HashRouter>
  );
}

const styles: Record<string, React.CSSProperties> = {
  app: {
    minHeight: '100vh',
    background: '#0d1117',
    color: '#c9d1d9',
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  },
  header: {
    background: '#161b22',
    borderBottom: '1px solid #30363d',
    padding: '0 24px',
    height: 52,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 24,
  },
  logo: {
    fontSize: 16,
    fontWeight: 600,
    color: '#58a6ff',
    textDecoration: 'none',
  },
  nav: {
    display: 'flex',
    gap: 4,
  },
  navLink: {
    color: '#8b949e',
    textDecoration: 'none',
    fontSize: 13,
    padding: '6px 12px',
    borderRadius: 6,
    transition: 'all 0.15s',
  },
  headerRight: {
    display: 'flex',
    alignItems: 'center',
  },
  health: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 12,
    color: '#8b949e',
  },
  healthDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
  },
  main: {
    maxWidth: 1200,
    margin: '0 auto',
  },
};
