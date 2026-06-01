import { Navigate, Routes, Route } from 'react-router-dom'
import { PicksPage } from './pages/PicksPage'
import { PositionsPage } from './pages/PositionsPage'
import { StockDetailPage } from './pages/StockDetailPage'
import { DataHealthPage } from './pages/DataHealthPage'
import { BacktestPage } from './pages/BacktestPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<PicksPage />} />
      <Route path="/positions" element={<PositionsPage />} />
      <Route path="/stock/:symbol" element={<StockDetailPage />} />
      <Route path="/health" element={<DataHealthPage />} />
      <Route path="/backtest" element={<BacktestPage />} />
      {/* legacy URL — old bookmarks land on Backtest */}
      <Route path="/simulate" element={<Navigate to="/backtest" replace />} />
    </Routes>
  )
}
