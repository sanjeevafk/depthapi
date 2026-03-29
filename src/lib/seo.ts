const rawSiteName = import.meta.env.VITE_SITE_NAME;
const rawBaseUrl = import.meta.env.VITE_PUBLIC_BASE_URL;
const rawOgImage = import.meta.env.VITE_OG_IMAGE_URL;

export const getSiteName = () => rawSiteName || "KnowBear";

export const getBaseUrl = () =>
  (rawBaseUrl || "https://yourdomain.com").replace(/\/+$/, "");

export const getOgImageUrl = () => {
  if (rawOgImage) return rawOgImage;
  return `${getBaseUrl()}/og-image.png`;
};

export const buildTitle = (title: string) => `${title} | ${getSiteName()}`;
