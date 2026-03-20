import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { persistQueryClient } from "@tanstack/react-query-persist-client";
import { createSyncStoragePersister } from "@tanstack/query-sync-storage-persister";
import { AuthProvider } from "./context/AuthContext";
import { UsageGateProvider } from "./context/UsageGateContext";
import { ModeProvider } from "./context/ModeContext";
import "./index.css";
import App from "./App.tsx";
import { QUERY_PERSIST_KEY } from "./lib/queryPersistence";
import { initMonitoring } from "./lib/monitoring";

const root = document.getElementById("root");
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
    },
  },
});

if (typeof window !== "undefined") {
  const persister = createSyncStoragePersister({
    storage: window.localStorage,
    key: QUERY_PERSIST_KEY,
  });
  persistQueryClient({
    queryClient,
    persister,
    buster: "v1",
    maxAge: 1000 * 60 * 60 * 24,
  });
}

if (!root) {
  throw new Error("Root element not found");
}

initMonitoring();

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <UsageGateProvider>
            <ModeProvider>
              <App />
            </ModeProvider>
          </UsageGateProvider>
        </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
);
