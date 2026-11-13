import { useState } from "react";
import KpiPanel from "./components/KpiPanel";
import BuildingView from "./components/BuildingView";
import ReviewQueue from "./components/ReviewQueue";
import Chat from "./components/Chat";
import PayrollLog from "./components/PayrollLog";
import SourceDrawer, { DrawerRequest } from "./components/SourceDrawer";

const TABS = ["KPIs", "Building view", "Review queue", "Payroll & exceptions", "Ask the data"] as const;
type Tab = (typeof TABS)[number];

// The page: five tabs, plus one drawer that shows the source records behind any figure.
export default function App() {
  const [tab, setTab] = useState<Tab>("KPIs");
  // Any component can open the drawer to show the source records behind a figure.
  const [drawer, setDrawer] = useState<DrawerRequest | null>(null);

  return (
    <div className="app">
      <header>
        <h1>Operations data spine</h1>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {tab === "KPIs" && <KpiPanel open={setDrawer} />}
        {tab === "Building view" && <BuildingView open={setDrawer} />}
        {tab === "Review queue" && <ReviewQueue open={setDrawer} />}
        {tab === "Payroll & exceptions" && <PayrollLog open={setDrawer} />}
        {tab === "Ask the data" && <Chat open={setDrawer} />}
      </main>
      {drawer && <SourceDrawer request={drawer} onClose={() => setDrawer(null)} />}
    </div>
  );
}
