import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Legend,
} from "recharts";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../context/AuthContext";

const API_BASE_URL = import.meta.env.VITE_API_URL || "";

type UsageItem = {
  id: string;
  request_id?: string;
  user_id?: string;
  conversation_id?: string;
  model_alias?: string;
  model_name?: string;
  provider?: string;
  mode?: string;
  status?: string;
  tokens_total?: number;
  estimated_cost_usd?: number;
  latency_ms?: number;
  created_at?: string;
  error_type?: string;
};

type CostItem = {
  bucket_start: string;
  model_alias?: string;
  mode?: string;
  total_cost_usd?: number;
  total_tokens?: number;
  request_count?: number;
};

type LatencyItem = {
  bucket_start: string;
  mode?: string;
  p50_latency_ms?: number;
  p95_latency_ms?: number;
  p99_latency_ms?: number;
};

type ErrorItem = {
  bucket_start: string;
  mode?: string;
  error_rate?: number;
  error_count?: number;
  request_count?: number;
};

type TopErrorItem = {
  error_type: string;
  error_message: string;
  error_count: number;
};

type SentryIssue = {
  id?: string;
  short_id?: string;
  title?: string;
  permalink?: string;
  count?: string;
  level?: string;
  status?: string;
};

const toDateInput = (date: Date): string =>
  date.toISOString().slice(0, 10);

const addDays = (date: Date, days: number): Date => {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
};

const formatDateLabel = (value?: string): string => {
  if (!value) return "";
  return new Date(value).toLocaleDateString();
};

const fetchWithAuth = async <T,>(
  url: string,
  token: string | null,
): Promise<T> => {
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token || ""}`,
      "Content-Type": "application/json",
    },
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
};

export default function AdminAnalyticsPage(): JSX.Element {
  const navigate = useNavigate();
  const { user, session, loading } = useAuth();
  const isAdmin =
    typeof user?.app_metadata?.role === "string" &&
    user.app_metadata.role.toLowerCase() === "admin";

  const [startDate, setStartDate] = useState<string>(() => {
    const today = new Date();
    return toDateInput(addDays(today, -7));
  });
  const [endDate, setEndDate] = useState<string>(() => {
    const today = new Date();
    return toDateInput(today);
  });

  const queryParams = useMemo(() => {
    const start = `${startDate}T00:00:00Z`;
    const end = `${endDate}T23:59:59Z`;
    return `start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  }, [startDate, endDate]);

  const token = session?.access_token || null;

  const usageQuery = useQuery({
    queryKey: ["analytics-usage", queryParams, token],
    queryFn: () =>
      fetchWithAuth<{ items: UsageItem[]; total: number }>(
        `${API_BASE_URL}/api/analytics/usage?${queryParams}&page=1&page_size=25`,
        token,
      ),
    enabled: Boolean(token && isAdmin),
  });

  const costQuery = useQuery({
    queryKey: ["analytics-cost", queryParams, token],
    queryFn: () =>
      fetchWithAuth<{ items: CostItem[] }>(
        `${API_BASE_URL}/api/analytics/cost?${queryParams}&bucket=day`,
        token,
      ),
    enabled: Boolean(token && isAdmin),
  });

  const latencyQuery = useQuery({
    queryKey: ["analytics-latency", queryParams, token],
    queryFn: () =>
      fetchWithAuth<{ items: LatencyItem[] }>(
        `${API_BASE_URL}/api/analytics/latency?${queryParams}&bucket=day`,
        token,
      ),
    enabled: Boolean(token && isAdmin),
  });

  const errorQuery = useQuery({
    queryKey: ["analytics-errors", queryParams, token],
    queryFn: () =>
      fetchWithAuth<{ items: ErrorItem[]; top_errors: TopErrorItem[] }>(
        `${API_BASE_URL}/api/analytics/errors?${queryParams}&bucket=day`,
        token,
      ),
    enabled: Boolean(token && isAdmin),
  });

  const sentryQuery = useQuery({
    queryKey: ["analytics-sentry", token],
    queryFn: () =>
      fetchWithAuth<{ issues: SentryIssue[] }>(
        `${API_BASE_URL}/api/analytics/sentry/issues?limit=10`,
        token,
      ),
    enabled: Boolean(token && isAdmin),
  });

  useEffect(() => {
    if (!loading && (!user || !isAdmin)) {
      navigate("/app");
    }
  }, [loading, user, isAdmin, navigate]);

  if (!loading && (!user || !isAdmin)) {
    return <div className="min-h-screen bg-black text-white">Redirecting...</div>;
  }

  const costItems = costQuery.data?.items ?? [];
  const latencyItems = latencyQuery.data?.items ?? [];
  const errorItems = errorQuery.data?.items ?? [];
  const usageItems = usageQuery.data?.items ?? [];

  const totalRequests = costItems.reduce(
    (sum, item) => sum + Number(item.request_count || 0),
    0,
  );
  const totalCost = costItems.reduce(
    (sum, item) => sum + Number(item.total_cost_usd || 0),
    0,
  );
  const avgLatency = latencyItems.reduce(
    (sum, item) => sum + Number(item.p50_latency_ms || 0),
    0,
  );
  const avgLatencyValue =
    latencyItems.length > 0 ? avgLatency / latencyItems.length : 0;
  const avgErrorRate = errorItems.reduce(
    (sum, item) => sum + Number(item.error_rate || 0),
    0,
  );
  const errorRateValue =
    errorItems.length > 0 ? (avgErrorRate / errorItems.length) * 100 : 0;

  const costTrend = costItems.map((item) => ({
    date: formatDateLabel(item.bucket_start),
    cost: Number(item.total_cost_usd || 0),
    requests: Number(item.request_count || 0),
  }));

  const costByModel = Object.values(
    costItems.reduce<Record<string, { model: string; cost: number }>>(
      (acc, item) => {
        const key = item.model_alias || "unknown";
        acc[key] = acc[key] || { model: key, cost: 0 };
        acc[key].cost += Number(item.total_cost_usd || 0);
        return acc;
      },
      {},
    ),
  );

  const latencyTrend = latencyItems.map((item) => ({
    date: formatDateLabel(item.bucket_start),
    p50: Number(item.p50_latency_ms || 0),
    p95: Number(item.p95_latency_ms || 0),
    p99: Number(item.p99_latency_ms || 0),
  }));

  const errorTrend = errorItems.map((item) => ({
    date: formatDateLabel(item.bucket_start),
    errorRate: Number(item.error_rate || 0) * 100,
  }));

  return (
    <div className="min-h-screen bg-black text-white px-6 py-10">
      <div className="max-w-6xl mx-auto space-y-10">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold">Analytics Dashboard</h1>
            <p className="text-sm text-zinc-400">
              Admin-only visibility into LLM usage and Sentry alerts.
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <div className="flex flex-col">
              <label className="text-xs text-zinc-400">Start</label>
              <input
                type="date"
                value={startDate}
                onChange={(event) => setStartDate(event.target.value)}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1"
              />
            </div>
            <div className="flex flex-col">
              <label className="text-xs text-zinc-400">End</label>
              <input
                type="date"
                value={endDate}
                onChange={(event) => setEndDate(event.target.value)}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1"
              />
            </div>
          </div>
        </div>

        <div className="grid md:grid-cols-4 gap-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-xs text-zinc-400">Total Requests</p>
            <p className="text-2xl font-semibold">{totalRequests}</p>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-xs text-zinc-400">Total Cost</p>
            <p className="text-2xl font-semibold">${totalCost.toFixed(2)}</p>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-xs text-zinc-400">Avg p50 Latency</p>
            <p className="text-2xl font-semibold">{avgLatencyValue.toFixed(0)} ms</p>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <p className="text-xs text-zinc-400">Avg Error Rate</p>
            <p className="text-2xl font-semibold">{errorRateValue.toFixed(2)}%</p>
          </div>
        </div>

        <div className="grid lg:grid-cols-2 gap-6">
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 h-72">
            <h2 className="text-sm font-semibold mb-3">Requests & Cost Trend</h2>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={costTrend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="date" stroke="#a1a1aa" />
                <YAxis stroke="#a1a1aa" />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="requests" stroke="#38bdf8" strokeWidth={2} />
                <Line type="monotone" dataKey="cost" stroke="#f97316" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 h-72">
            <h2 className="text-sm font-semibold mb-3">Cost by Model</h2>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={costByModel}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="model" stroke="#a1a1aa" />
                <YAxis stroke="#a1a1aa" />
                <Tooltip />
                <Bar dataKey="cost" fill="#f97316" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="grid lg:grid-cols-2 gap-6">
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 h-72">
            <h2 className="text-sm font-semibold mb-3">Latency Percentiles</h2>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={latencyTrend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="date" stroke="#a1a1aa" />
                <YAxis stroke="#a1a1aa" />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="p50" stroke="#22c55e" strokeWidth={2} />
                <Line type="monotone" dataKey="p95" stroke="#eab308" strokeWidth={2} />
                <Line type="monotone" dataKey="p99" stroke="#ef4444" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 h-72">
            <h2 className="text-sm font-semibold mb-3">Error Rate Trend</h2>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={errorTrend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="date" stroke="#a1a1aa" />
                <YAxis stroke="#a1a1aa" />
                <Tooltip />
                <Line type="monotone" dataKey="errorRate" stroke="#ef4444" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="grid lg:grid-cols-2 gap-6">
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <h2 className="text-sm font-semibold mb-3">Recent Requests</h2>
            <div className="overflow-auto">
              <table className="min-w-full text-sm text-left">
                <thead className="text-xs text-zinc-400">
                  <tr>
                    <th className="py-2 pr-4">Time</th>
                    <th className="py-2 pr-4">Model</th>
                    <th className="py-2 pr-4">Mode</th>
                    <th className="py-2 pr-4">Tokens</th>
                    <th className="py-2 pr-4">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {usageItems.map((item) => (
                    <tr key={item.id} className="border-t border-zinc-800">
                      <td className="py-2 pr-4 text-xs text-zinc-400">
                        {formatDateLabel(item.created_at)}
                      </td>
                      <td className="py-2 pr-4">{item.model_alias || "unknown"}</td>
                      <td className="py-2 pr-4">{item.mode || "-"}</td>
                      <td className="py-2 pr-4">{item.tokens_total || 0}</td>
                      <td className="py-2 pr-4">{item.status || "-"}</td>
                    </tr>
                  ))}
                  {!usageItems.length && (
                    <tr>
                      <td className="py-3 text-zinc-500" colSpan={5}>
                        No data available.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
            <h2 className="text-sm font-semibold mb-3">Sentry Issues</h2>
            <ul className="space-y-3">
              {(sentryQuery.data?.issues || []).map((issue) => (
                <li key={issue.id} className="border border-zinc-800 rounded p-3">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium">
                        {issue.title || "Untitled issue"}
                      </p>
                      <p className="text-xs text-zinc-500">
                        {issue.short_id} · {issue.level} · {issue.status}
                      </p>
                    </div>
                    {issue.permalink && (
                      <a
                        href={issue.permalink}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-sky-400 hover:underline"
                      >
                        Open in Sentry
                      </a>
                    )}
                  </div>
                </li>
              ))}
              {!(sentryQuery.data?.issues || []).length && (
                <li className="text-sm text-zinc-500">
                  No Sentry issues found.
                </li>
              )}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
