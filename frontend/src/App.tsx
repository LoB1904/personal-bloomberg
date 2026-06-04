import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Screener from './pages/Screener'
import TrackRecord from './pages/TrackRecord'
import Ticker from './pages/Ticker'
import Portfolio from './pages/Portfolio'

export default function App() {
  return (
    <BrowserRouter>
      <Toaster
        position="top-right"
        toastOptions={{ style: { background: '#1e293b', color: '#f1f5f9' } }}
      />
      <Routes>
        <Route element={<Layout />}>
          <Route path="/"             element={<Dashboard />} />
          <Route path="/screener"     element={<Screener />} />
          <Route path="/track-record" element={<TrackRecord />} />
          <Route path="/portfolio"    element={<Portfolio />} />
          <Route path="/ticker/:symbol" element={<Ticker />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
