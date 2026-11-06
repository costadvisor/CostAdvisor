import { Component } from 'react';

const SUPPORT_EMAIL = 'alexis@staminachem.com';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  // The route-level boundary in App passes the pathname: navigating away
  // (Back, a nav link) clears the error instead of leaving it up for good.
  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error, info) {
    // If Sentry is wired on the window, report the crash.
    if (typeof window !== 'undefined' && window.Sentry?.captureException) {
      window.Sentry.captureException(error, { extra: info });
    }
    // eslint-disable-next-line no-console
    console.error('Unhandled UI error:', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="ca-page ca-fade-in" style={{ maxWidth: 560, margin: '10vh auto', textAlign: 'center' }}>
        <div className="ca-h1">Something went wrong</div>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.7 }}>
          The app hit an unexpected error. You can try reloading. If it keeps happening,
          email us and include roughly what you were doing when it broke.
        </p>
        <p style={{ fontSize: 13, marginTop: 16 }}>
          <a href={`mailto:${SUPPORT_EMAIL}`} style={{ color: 'var(--accent)' }}>{SUPPORT_EMAIL}</a>
        </p>
        <button
          className="ca-btn ca-btn-primary"
          onClick={() => window.location.reload()}
          style={{ marginTop: 16 }}
        >
          Reload
        </button>
      </div>
    );
  }
}
