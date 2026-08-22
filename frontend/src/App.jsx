import { Routes, Route, Navigate } from 'react-router-dom';
import ProtectedRoute from './ProtectedRoute';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import ImpersonationBar from './components/ImpersonationBar';
import OnboardingChecklist from './components/OnboardingChecklist';
import { ToastProvider } from './components/Toast';
import Login from './pages/Login';
import CostModelBuilder from './pages/CostModelBuilder';
import Evolution from './pages/Evolution';
import Squeeze from './pages/Squeeze';

import Brief from './pages/Brief';
import Pricing from './pages/Pricing';
import Dashboard from './pages/Dashboard';
import Suppliers from './pages/Suppliers';
import SupplierPurchases from './pages/SupplierPurchases';
import Products from './pages/Products';
import Admin from './pages/Admin';
import Formulas from './pages/Formulas';
import Alerts from './pages/Alerts';
import Team from './pages/Team';
import Privacy from './pages/Privacy';
import Profile from './pages/Profile';
import Terms from './pages/Terms';
import NotFound from './pages/NotFound';
import IndexLibraryArea from './pages/workspace/IndexLibraryArea';
import PortfolioArea from './pages/workspace/PortfolioArea';
import ProductDetailArea from './pages/workspace/ProductDetailArea';
import MonitorArea from './pages/workspace/MonitorArea';
import ForecastArea from './pages/workspace/ForecastArea';
import NegotiateArea from './pages/workspace/NegotiateArea';
import NegotiateDetailArea from './pages/workspace/NegotiateDetailArea';
import IntelligenceArea from './pages/workspace/IntelligenceArea';
import IntelligenceDetailArea from './pages/workspace/IntelligenceDetailArea';
import { useAuth } from './AuthContext';

export default function App() {
  const { user } = useAuth();

  return (
    <ToastProvider>
      {user && <Navbar />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/terms" element={<Terms />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<Navigate to="/team" replace />} />
            <Route path="/cost-models/new" element={<CostModelBuilder />} />
            <Route path="/cost-models/:costModelId" element={<CostModelBuilder />} />
            <Route path="/cost-models/:costModelId/evolution" element={<Evolution />} />

            <Route path="/cost-models/:costModelId/brief" element={<Brief />} />
            <Route path="/cost-models/:costModelId/pricing" element={<Pricing />} />
            <Route path="/cost-models/:costModelId/squeeze" element={<Squeeze />} />
            <Route path="/indexes" element={<Navigate to="/index-library" replace />} />
            <Route path="/products" element={<Products />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/suppliers" element={<Suppliers />} />
            <Route path="/suppliers/:supplierId/purchases" element={<SupplierPurchases />} />
            <Route path="/team" element={<Team />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/formulas" element={<Formulas />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/fx-rates" element={<Navigate to="/index-library" replace />} />
            <Route path="/index-library" element={<IndexLibraryArea />} />
            <Route path="/portfolio" element={<PortfolioArea />} />
            <Route path="/portfolio/:costModelId" element={<ProductDetailArea />} />
            <Route path="/monitor" element={<MonitorArea />} />
            <Route path="/forecast" element={<ForecastArea />} />
            <Route path="/negotiate" element={<NegotiateArea />} />
            <Route path="/negotiate/:costModelId" element={<NegotiateDetailArea />} />
            <Route path="/intelligence" element={<IntelligenceArea />} />
            <Route path="/intelligence/:costModelId" element={<IntelligenceDetailArea />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
        {user && <ImpersonationBar />}
        {user && <OnboardingChecklist />}
      </div>
      <Footer />
    </ToastProvider>
  );
}
