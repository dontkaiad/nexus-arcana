import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'

if (typeof window !== 'undefined' && window.Telegram?.WebApp?.ready) {
  try { window.Telegram.WebApp.ready() } catch { /* noop */ }
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary label="root">
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
