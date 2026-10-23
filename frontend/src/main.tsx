import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { PairScreen } from './components/PairScreen'
import { checkPaired, usePaired } from './lib/pairing'

function Root() {
  const paired = usePaired()
  if (paired === 'checking') return null
  return paired === 'paired' ? <App /> : <PairScreen />
}

checkPaired()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
