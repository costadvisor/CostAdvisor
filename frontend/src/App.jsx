import { Routes, Route, Navigate, useLocation, useParams } from 'react-router-dom';
import ProtectedRoute, { RequireTeam } from './ProtectedRoute';
import Navbar from './components/Navbar';
import ErrorBoundary from './components/ErrorBoundary';
import Footer from './components/Footer';
import ImpersonationBar from './components/ImpersonationBar';
import OnboardingChecklist from './components/OnboardingChecklist';
import { ToastProvider } from './components/Toast';
import Login from './pages/Login';
import CostModelBuilder from './pages/CostModelBuilder';
import Evolution from './pages/Evolution';
import Squeeze from './pages/Squeeze';

import Pricing from './pages/Pricing';
import Dashboard from './pages/Dashboard';
import Suppliers from './pages/Suppliers';
import SupplierPurchases from './pages/SupplierPurchases';
import Products from './pages/Products';
import Admin from './pages/Admin';
import Formulas from './pages/Formulas';
import Alerts from './pages/Alerts';
import QuoteExtraction from './pages/QuoteExtraction';
import Contracts from './pages/Contracts';
import Curation from './pages/Curation';
import Support from './pages/Support';
import SupportConsole from './pages/SupportConsole';
import Scenarios from './pages/Scenarios';
import Dimensions from './pages/Dimensions';
import Validation from './pages/Validation';
import PriceListImport from './pages/PriceListImport';
import IndexSourcing from './pages/IndexSourcing';
import RefreshStatus from './pages/RefreshStatus';
import AiCostModeler from './pages/AiCostModeler';
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
import NegotiationPrepArea from './pages/workspace/NegotiationPrepArea';
import IntelligenceArea from './pages/workspace/IntelligenceArea';
import IntelligenceComboArea from './pages/workspace/IntelligenceComboArea';
import WhatsLeft from './pages/WhatsLeft';
import ProductsPage from './pages/intel/ProductsPage';
import ProductDetailPage from './pages/intel/ProductDetailPage';
import LinesPage from './pages/intel/LinesPage';
import LineDetailPage from './pages/intel/LineDetailPage';
import CategoriesPage from './pages/intel/CategoriesPage';
import IndustryDetailPage from './pages/intel/IndustryDetailPage';
import SuppliersPage from './pages/intel/SuppliersPage';
import SupplierDetailPage from './pages/intel/SupplierDetailPage';
import StrategyLandingPage from './pages/strategy/StrategyLandingPage';
import StrategyCategoryPage from './pages/strategy/StrategyCategoryPage';
import { useAuth } from './AuthContext';

// The legacy brief page left the Negotiation journey and had no link to prep;
// old links and bookmarks land on the Negotiate brief, query kept.
function LegacyBriefRedirect() {
  const { costModelId } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/negotiate/${costModelId}${search}`} replace />;
}

/* One builder per model: unkeyed, moving between /cost-models/new and a model
 * (or two models) kept the last one's state, such as its not-found card or
 * its formula on the New page. */
function CostModelBuilderRoute() {
  const { costModelId } = useParams();
  return <CostModelBuilder key={costModelId || 'new'} />;
}

export default function App() {
  const { user } = useAuth();
  const location = useLocation();

  return (
    <ToastProvider>
      {user && <Navbar />}
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* A page crash replaces only the page: the nav stays, and moving to
            another path clears it. The boundary in main.jsx is the last resort. */}
        <ErrorBoundary resetKey={location.pathname}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/privacy" element={<Privacy />} />
            <Route path="/terms" element={<Terms />} />
            <Route element={<ProtectedRoute />}>
              {/* These work without a team: /team is where a team-less user
                  creates or joins one. */}
              <Route path="/" element={<Navigate to="/team" replace />} />
              <Route path="/team" element={<Team />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="/admin" element={<Admin />} />
              {/* Handles "no team" itself: the FAQ still works, and the form
                  gives way to the support email. */}
              <Route path="/support" element={<Support />} />
              {/* Its own route, not a tab inside /admin: that page is
                  super-admin-only and every other tab on it calls
                  super-admin endpoints. See pages/SupportConsole.jsx. */}
              <Route path="/support-console" element={<SupportConsole />} />
              <Route path="/validation" element={<Validation />} />
              <Route path="/index-sourcing" element={<IndexSourcing />} />
              {/* Jira 209: super-admin page; the page itself checks the role. */}
              <Route path="/refresh-status" element={<RefreshStatus />} />
              {/* Intelligence layer (demo build, DEMO_BUILD_SPEC §7). The static
                  segments below outrank the `:costModelId` param route — react-router
                  6 ranks by specificity, not order, across both layout groups — so
                  /intelligence/products never resolves as a cost model id. The
                  catalogue is shared reference data, so it needs no team. */}
              <Route path="/intelligence" element={<Navigate to="/intelligence/products" replace />} />
              <Route path="/intelligence/products" element={<ProductsPage />} />
              <Route path="/intelligence/products/:pid" element={<ProductDetailPage />} />
              <Route path="/intelligence/lines" element={<LinesPage />} />
              <Route path="/intelligence/lines/:lineRef" element={<LineDetailPage />} />
              <Route path="/intelligence/categories" element={<CategoriesPage />} />
              <Route path="/intelligence/categories/:industrySlug" element={<IndustryDetailPage />} />
              <Route path="/intelligence/suppliers" element={<SuppliersPage />} />
              <Route path="/intelligence/suppliers/:id" element={<SupplierDetailPage />} />

              {/* Everything below reads team data and waits for a team. */}
              <Route element={<RequireTeam />}>
                <Route path="/cost-models/new" element={<CostModelBuilderRoute />} />
                <Route path="/cost-models/:costModelId" element={<CostModelBuilderRoute />} />
                <Route path="/cost-models/:costModelId/evolution" element={<Evolution />} />

                <Route path="/cost-models/:costModelId/brief" element={<LegacyBriefRedirect />} />
                <Route path="/cost-models/:costModelId/pricing" element={<Pricing />} />
                <Route path="/cost-models/:costModelId/squeeze" element={<Squeeze />} />
                <Route path="/indexes" element={<Navigate to="/index-library" replace />} />
                <Route path="/products" element={<Products />} />
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/suppliers" element={<Suppliers />} />
                <Route path="/suppliers/:supplierId/purchases" element={<SupplierPurchases />} />
                <Route path="/formulas" element={<Formulas />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/quotes" element={<QuoteExtraction />} />
                <Route path="/contracts" element={<Contracts />} />
                <Route path="/curation" element={<Curation />} />
                <Route path="/scenarios" element={<Scenarios />} />
                <Route path="/dimensions" element={<Dimensions />} />
                <Route path="/price-lists" element={<PriceListImport />} />
                <Route path="/ai-cost-modeler" element={<AiCostModeler />} />
                <Route path="/fx-rates" element={<Navigate to="/index-library" replace />} />
                <Route path="/index-library" element={<IndexLibraryArea />} />
                <Route path="/portfolio" element={<PortfolioArea />} />
                <Route path="/portfolio/:costModelId" element={<ProductDetailArea />} />
                <Route path="/monitor" element={<MonitorArea />} />
                <Route path="/forecast" element={<ForecastArea />} />
                <Route path="/negotiate" element={<NegotiateArea />} />
                <Route path="/negotiate/:costModelId/prep" element={<NegotiationPrepArea />} />
                <Route path="/negotiate/:costModelId" element={<NegotiateDetailArea />} />
                {/* The Wave 3 combo grid used to own /intelligence; it keeps a home here. */}
                <Route path="/intelligence/combos" element={<IntelligenceArea />} />
                {/* Combo grain is the library's own; the cost-model route resolves a
                    product to the same combo and reports how it got there. */}
                <Route path="/intelligence/combo/:templateId/:region" element={<IntelligenceComboArea />} />
                <Route path="/intelligence/:costModelId" element={<IntelligenceComboArea />} />
                <Route path="/strategy" element={<StrategyLandingPage />} />
                <Route path="/strategy/:slug" element={<StrategyCategoryPage />} />
              </Route>

              {/* Internal status page, super admins only: anyone else gets the
                  plain 404 rather than a hint that the page exists. */}
              <Route path="/preview" element={user?.is_super_admin ? <WhatsLeft /> : <NotFound />} />
            </Route>
            <Route path="*" element={<NotFound />} />
          </Routes>
        </ErrorBoundary>
        {user && <ImpersonationBar />}
        {user && <OnboardingChecklist />}
      </div>
      <Footer />
    </ToastProvider>
  );
}
