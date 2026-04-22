import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'

if (typeof window !== 'undefined' && window.Telegram?.WebApp?.ready) {
  try { window.Telegram.WebApp.ready() } catch { /* noop */ }
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
