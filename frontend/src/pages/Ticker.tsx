import { useParams } from 'react-router-dom'

export default function Ticker() {
  const { symbol } = useParams<{ symbol: string }>()
  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-2">Ticker: {symbol}</h1>
      <p className="text-slate-400">Scheda ticker — Fase 3 in costruzione.</p>
    </div>
  )
}
