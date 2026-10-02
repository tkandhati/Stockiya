import { Navigate, Routes, Route } from 'react-router-dom'
import { PicksPage } from './pages/PicksPage'
import { StockDetailPage } from './pages/StockDetailPage'

// SIMPLE MODE (owner ask, 2026-10-02). The whole app is trimmed to the two
// sections on the Picks page — (1) Today's picks and (2) Pullback Re-Entry
// Setups on the last ~45-60 days of picks — plus the per-pick drill-down. Every
// other tab/page (My positions, Price trend, Backtest, Data health) is removed
// from routing. The page files are kept on disk (NOT deleted) so this is fully
// reversible — restore a <Route> below and the matching StrategyTabs link:
//   import { PositionsPage } from './pages/PositionsPage'
//   import { DataHealthPage } from './pages/DataHealthPage'
//   import { BacktestPage } from './pages/BacktestPage'
//   import { PriceTrendPage } from './features/price-trend/PriceTrendPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<PicksPage />} />
      <Route path="/stock/:symbol" element={<StockDetailPage />} />
      {/* Any other path (old tabs + stale bookmarks) lands on the Picks page. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
