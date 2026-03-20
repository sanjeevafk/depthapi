import { Suspense, lazy } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import ToastHost from "./components/ToastHost";
import { setMonitoringRoute } from "./lib/monitoring";
import { useEffect } from "react";

const LandingPage = lazy(() => import("./pages/LandingPage"));
const AppPage = lazy(() => import("./pages/AppPage"));
const SuccessPage = lazy(() => import("./pages/SuccessPage"));

function RouteMonitoringBridge(): null {
  const location = useLocation();

  useEffect(() => {
    setMonitoringRoute(`${location.pathname}${location.search}`);
  }, [location.pathname, location.search]);

  return null;
}

export default function App(): JSX.Element {
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
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
