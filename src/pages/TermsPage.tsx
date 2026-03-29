import LegalPageLayout from "../components/LegalPageLayout";
import { Helmet } from "react-helmet-async";

export default function TermsPage(): JSX.Element {
  return (
    <LegalPageLayout title="Terms of Service" lastUpdated="March 29, 2026">
      <Helmet>
        <title>KnowBear Terms of Service | AI Learning SaaS</title>
        <meta
          name="description"
          content="Review the KnowBear Terms of Service for our AI learning workspace, including account use, subscriptions, payments, and legal terms."
        />
        <meta property="og:title" content="KnowBear Terms of Service" />
      </Helmet>
      <section className="space-y-4">
        <p>
          These Terms of Service ("Terms") govern your access to and use of the
          KnowBear application, website, and related services (collectively, the
          "Service"). By creating an account or using the Service, you agree to
          be bound by these Terms.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Account Usage
        </h2>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            You must sign in with a Google account and provide accurate
            information during onboarding.
          </li>
          <li>
            You are responsible for all activity that occurs under your account
            and for maintaining the confidentiality of your login credentials.
          </li>
          <li>
            You may not use the Service for unlawful, harmful, or abusive
            purposes or in ways that interfere with other users.
          </li>
        </ul>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Subscriptions & Cancellations
        </h2>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            Paid plans are billed on a monthly basis and renew automatically
            unless canceled prior to the next billing date.
          </li>
          <li>
            You can cancel anytime from your account settings; access continues
            through the end of the current billing period.
          </li>
          <li>
            We may update plan features or pricing with advance notice posted in
            the Service or via email.
          </li>
        </ul>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Payments via Dodo
        </h2>
        <p>
          Subscription payments are processed by Dodo Payments. We do not store
          full payment card details on our servers. By subscribing, you agree to
          Dodo&apos;s applicable payment terms and authorize Dodo to charge your
          selected payment method according to your plan.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Intellectual Property
        </h2>
        <ul className="list-disc space-y-2 pl-5">
          <li>
            The Service, including its software, branding, and content, is owned
            by KnowBear and its licensors.
          </li>
          <li>
            You retain ownership of any content you submit, but you grant
            KnowBear a limited license to host, process, and display it to
            provide the Service.
          </li>
        </ul>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Limitation of Liability
        </h2>
        <p>
          To the maximum extent permitted by law, KnowBear is not liable for any
          indirect, incidental, special, or consequential damages, or for any
          loss of profits, data, or goodwill arising out of or related to your
          use of the Service.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Governing Law
        </h2>
        <p>
          These Terms are governed by the laws of the jurisdiction in which
          KnowBear is established, without regard to conflict of law rules. You
          agree that any disputes will be resolved in the courts of that
          jurisdiction.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-white sm:text-xl">
          Contact
        </h2>
        <p>
          Questions about these Terms can be sent to
          {" "}
          <span className="font-semibold">contact@knowbear.app</span>.
        </p>
      </section>
    </LegalPageLayout>
  );
}
