import { Suspense, lazy } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import ToastHost from "./components/ToastHost";
import { setMonitoringRoute } from "./lib/monitoring";
import { trackPageView } from "./lib/analytics";
import { useEffect } from "react";
import { useChatStore } from "./stores/useChatStore";
import { loadTheme } from "./lib/chatStoreUtils";

const LandingPage = lazy(() => import("./pages/LandingPage"));
const AppPage = lazy(() => import("./pages/AppPage"));
const SuccessPage = lazy(() => import("./pages/SuccessPage"));
const AdminAnalyticsPage = lazy(() => import("./pages/AdminAnalyticsPage"));
const TermsPage = lazy(() => import("./pages/TermsPage"));
const PrivacyPage = lazy(() => import("./pages/PrivacyPage"));
const PricingPage = lazy(() => import("./pages/PricingPage"));
const FeaturesPage = lazy(() => import("./pages/FeaturesPage"));

function RouteMonitoringBridge(): null {
  const location = useLocation();

  useEffect(() => {
    setMonitoringRoute(`${location.pathname}${location.search}`);
    const handle = window.setTimeout(() => {
      trackPageView(`${location.pathname}${location.search}`, document.title);
    }, 0);
    return () => window.clearTimeout(handle);
  }, [location.pathname, location.search]);

  return null;
}

export default function App(): JSX.Element {
  const setTheme = useChatStore((state) => state.setTheme);

  useEffect(() => {
    setTheme(loadTheme());
  }, [setTheme]);

  return (
    <ErrorBoundary>
      <RouteMonitoringBridge />
      <ToastHost />
      <Suspense
        fallback={
          <div className="min-h-screen bg-black text-white flex items-center justify-center">
            Loading...
          </div>
        }
      >
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/app" element={<AppPage />} />
          <Route path="/success" element={<SuccessPage />} />
          <Route path="/admin/analytics" element={<AdminAnalyticsPage />} />
          <Route path="/terms" element={<TermsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/pricing" element={<PricingPage />} />
          <Route path="/features" element={<FeaturesPage />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
