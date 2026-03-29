import LegalPageLayout from "../components/LegalPageLayout";
import { Helmet } from "react-helmet-async";

export default function PrivacyPage(): JSX.Element {
  return (
    <LegalPageLayout title="Privacy Policy" lastUpdated="March 29, 2026">
      <Helmet>
        <title>KnowBear Privacy Policy | AI Learning SaaS</title>
        <meta
          name="description"
          content="Read the KnowBear Privacy Policy to understand what data we collect, how we use it, and your rights under GDPR/CCPA."
        />
        <meta property="og:title" content="KnowBear Privacy Policy" />
      </Helmet>
      <section className="space-y-4">
        <p>
          This Privacy Policy explains how KnowBear collects, uses, and shares
          information when you use our Service.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Information We Collect
        </h2>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="font-semibold">Account data:</span> your email
            address and basic profile details provided by Google Authentication.
          </li>
          <li>
            <span className="font-semibold">Usage data:</span> activity logs,
            feature usage, and device/browser metadata to improve performance and
            reliability.
          </li>
          <li>
            <span className="font-semibold">Payment data:</span> subscription
            status and billing metadata provided by Dodo Payments. We do not
            store full payment card details.
          </li>
        </ul>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          How We Use Information
        </h2>
        <ul className="list-disc space-y-2 pl-5">
          <li>Provide and secure access to your account and conversations.</li>
          <li>Process subscriptions and manage billing status.</li>
          <li>Improve product performance, reliability, and user experience.</li>
          <li>Communicate service updates, billing notices, and policy changes.</li>
        </ul>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Service Providers
        </h2>
        <p>
          We rely on trusted processors to deliver the Service:
        </p>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <span className="font-semibold">Supabase</span> for authentication
            and database hosting.
          </li>
          <li>
            <span className="font-semibold">Dodo Payments</span> for payment
            processing and subscription management.
          </li>
        </ul>
        <p>
          These providers process data on our behalf under contractual
          obligations consistent with this policy.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Cookies
        </h2>
        <p>
          We use essential cookies and similar technologies to keep you signed
          in, maintain session security, and understand aggregate usage
          patterns. You can control cookies through your browser settings, but
          some features may not function properly without them.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Your Rights (GDPR/CCPA)
        </h2>
        <ul className="list-disc space-y-2 pl-5">
          <li>Request access to, correction of, or deletion of your data.</li>
          <li>Object to or restrict certain processing activities.</li>
          <li>Request a portable copy of your personal information.</li>
          <li>Opt out of the sale of personal information (we do not sell it).</li>
        </ul>
        <p>
          To exercise these rights, contact us at
          {" "}
          <span className="font-semibold">contact@knowbear.app</span>.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Contact
        </h2>
        <p>
          If you have questions about this Privacy Policy or our data practices,
          reach out to
          {" "}
          <span className="font-semibold">contact@knowbear.app</span>.
        </p>
      </section>
    </LegalPageLayout>
  );
}
