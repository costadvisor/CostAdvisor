import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
// Shared stylesheets first: every page stylesheet is imported (through App)
// after these, so a page rule wins over a shared rule of equal specificity.
import './styles.css';
import './styles/intel.css';
import App from './App';
import { AuthProvider } from './AuthContext';
import ErrorBoundary from './components/ErrorBoundary';
import { ConfirmProvider } from './components/ConfirmDialog';
import { applyTheme, getCachedTheme } from './utils/theme';

applyTheme(getCachedTheme());

// Registered after `load` so it never competes with the initial render for
// bandwidth/CPU; a failed registration (unsupported browser, dev server
// quirks) is non-fatal, so it's swallowed rather than surfaced.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
);
