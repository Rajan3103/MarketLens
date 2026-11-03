// Settings & Status: keys, trading mode and database (the live part is components/SettingsStatus.jsx).
import SettingsStatus from "@/components/SettingsStatus";

export const metadata = { title: "Settings & Status" };

export default function SettingsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">⚙️ Settings &amp; Status</h1>
      <SettingsStatus />
    </div>
  );
}
