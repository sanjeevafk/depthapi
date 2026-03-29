import { Suspense, lazy } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import ToastHost from "./components/ToastHost";
import { setMonitoringRoute } from "./lib/monitoring";
import { useEffect } from "react";
import { useChatStore } from "./stores/useChatStore";
import { loadTheme } from "./lib/chatStoreUtils";

const LandingPage = lazy(() => import("./pages/LandingPage"));
const AppPage = lazy(() => import("./pages/AppPage"));
const SuccessPage = lazy(() => import("./pages/SuccessPage"));
const AdminAnalyticsPage = lazy(() => import("./pages/AdminAnalyticsPage"));
const TermsPage = lazy(() => import("./pages/TermsPage"));
const PrivacyPage = lazy(() => import("./pages/PrivacyPage"));

function RouteMonitoringBridge(): null {
  const location = useLocation();

  useEffect(() => {
    setMonitoringRoute(`${location.pathname}${location.search}`);
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
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
