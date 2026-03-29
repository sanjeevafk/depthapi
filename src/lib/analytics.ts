import ReactGA from "react-ga4";

const GA_MEASUREMENT_ID = import.meta.env.VITE_GA4_ID;
const IS_PROD = import.meta.env.PROD;

let isInitialized = false;

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

const ensureGtag = () => {
  if (!IS_PROD || !GA_MEASUREMENT_ID || typeof document === "undefined") {
    return;
  }

  if (document.getElementById("ga4-script")) {
    return;
  }

  const script = document.createElement("script");
  script.id = "ga4-script";
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`;
  document.head.appendChild(script);

  const inline = document.createElement("script");
  inline.id = "ga4-inline";
  inline.text = `
    window.dataLayer = window.dataLayer || [];
    function gtag(){window.dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', '${GA_MEASUREMENT_ID}', { send_page_view: false });
  `;
  document.head.appendChild(inline);
};

export const initAnalytics = () => {
  if (!IS_PROD || !GA_MEASUREMENT_ID) {
    return;
  }
  if (isInitialized) {
    return;
  }
  ensureGtag();
  ReactGA.initialize(GA_MEASUREMENT_ID);
  isInitialized = true;
};

const canTrack = () => IS_PROD && Boolean(GA_MEASUREMENT_ID) && isInitialized;

type GtagEventParams = Record<string, unknown>;

type GtagFunction = (
  command: "event" | "config",
  eventNameOrId: string,
  params?: GtagEventParams,
) => void;

const gtagEvent = (eventName: string, params?: GtagEventParams) => {
  if (!canTrack() || typeof window === "undefined") return;
  const gtag = window.gtag as GtagFunction | undefined;
  if (!gtag) return;
  gtag("event", eventName, params);
};

export const trackPageView = (path: string, title?: string) => {
  if (!canTrack()) return;
  gtagEvent("page_view", {
    page_path: path,
    page_title: title,
    page_location: typeof window !== "undefined" ? window.location.href : path,
  });
};

export const trackSignUp = (method = "google") => {
  if (!canTrack()) return;
  gtagEvent("sign_up", { method });
};

export const trackSubscriptionStarted = (plan?: string) => {
  if (!canTrack()) return;
  gtagEvent("subscription_started", {
    plan,
    engagement_time_msec: 1,
  });
};

export const trackCustomEvent = (
  name: string,
  params?: Record<string, unknown>,
) => {
  if (!canTrack()) return;
  gtagEvent(name, params);
};
