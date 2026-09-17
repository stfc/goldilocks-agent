import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

// `#root` is defined statically in index.html, so it is always present at
// this point; the `!` documents that (rather than silently widening to
// `HTMLElement | null` and letting `createRoot` throw its own less clear
// error if the assumption were ever wrong).
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
