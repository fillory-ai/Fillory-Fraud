import { Activity, BarChart3, Bell, ClipboardPaste, Home, ScanLine, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type TabId = "dashboard" | "import" | "properties" | "listings" | "scans" | "alerts";

interface TopNavProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

const tabs = [
  { id: "dashboard", label: "Dashboard", icon: BarChart3 },
  { id: "import", label: "Import", icon: ClipboardPaste },
  { id: "properties", label: "Properties", icon: Home },
  { id: "listings", label: "Listings", icon: Search },
  { id: "scans", label: "Scans", icon: ScanLine },
  { id: "alerts", label: "Alerts", icon: Bell },
] as const;

export function TopNav({ activeTab, onTabChange }: TopNavProps) {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between md:px-6">
        <div className="flex items-center gap-3">
          <img src="/fillory-logo.png" alt="fillory" className="h-9 w-9 rounded-full object-cover shadow-sm" />
          <div><div className="font-semibold tracking-tight">fillory fraud detector</div><div className="flex items-center gap-1 text-xs text-muted-foreground"><Activity className="h-3 w-3" /> Rental monitoring</div></div>
        </div>
        <nav className="flex gap-1 overflow-x-auto" aria-label="Main navigation">
          {tabs.map(({ id, label, icon: Icon }) => (
            <Button key={id} variant="ghost" size="sm" onClick={() => onTabChange(id)} className={cn("shrink-0 gap-2 text-muted-foreground", activeTab === id && "bg-muted text-foreground")} aria-current={activeTab === id ? "page" : undefined}>
              <Icon className="h-4 w-4" />{label}
            </Button>
          ))}
        </nav>
      </div>
    </header>
  );
}
