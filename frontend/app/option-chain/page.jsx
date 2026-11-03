// Not built in the web app yet: shows what it will do (see lib/modules.js).
import ComingSoon from "@/components/ComingSoon";
import { moduleFor } from "@/lib/modules";

export const metadata = { title: moduleFor("/option-chain").title };

export default function OptionChainPage() {
  return <ComingSoon href="/option-chain" />;
}
