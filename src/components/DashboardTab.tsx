import { useState } from "react";
import { Activity, AlertTriangle, CheckCircle, Clock, EyeOff, FolderCheck, Home, Search, Settings, XCircle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ListingDetailDialog } from "@/components/ListingDetailDialog";
import { cn } from "@/lib/utils";
import type { ConfigStatusData, PropertyData, ScrapedListingData, StatsData } from "@/types";

interface DashboardTabProps { stats: StatsData | null; config: ConfigStatusData | null; listings: ScrapedListingData[]; properties: PropertyData[]; loading: boolean; }

const formatDate = (value: string | null | undefined) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Not yet";

export function DashboardTab({ stats, config, listings, properties, loading }: DashboardTabProps) {
  const [selected, setSelected] = useState<ScrapedListingData | null>(null);
  const flagged = listings.filter(l => l.fraud_status === "fraud");
  const health = stats?.scan_health;
  const showHealthBanner = health && !health.healthy;
  const metrics = [
    { label: "Properties", value: stats?.total_properties, icon: Home },
    { label: "Listings Scraped", value: stats?.total_listings_scraped, icon: Search },
    { label: "Fraud Detected", value: stats?.fraud_detected, icon: AlertTriangle },
    { label: "Open Cases", value: stats?.open_cases, icon: FolderCheck },
  ];
  const services = [
    ["Apify", config?.apify_configured, true], ["Twilio", config?.twilio_configured, config?.twilio_enabled], ["Gemini", config?.gemini_configured, true],
  ] as const;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div><h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1><p className="mt-1 text-sm text-muted-foreground">A real-time overview of your rental protection.</p></div>
        {config?.observe_mode && (
          <div className="flex items-center gap-1.5 rounded-md border border-blue-500/20 bg-blue-500/5 px-3 py-1.5 text-xs text-blue-600">
            <EyeOff className="h-3.5 w-3.5" />
            Observe mode — alerts recorded, not sent
          </div>
        )}
      </div>
      {/* A stale scanner means we are blind to the market, not that fraud is absent.
          The wording makes this distinction explicit so users don't read "0 fraud" as safe. */}
      {showHealthBanner && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Scanner is not current</AlertTitle>
          <AlertDescription>
            Last successful scan was {health.hours_since_success != null ? `${Math.round(health.hours_since_success)}h` : "an unknown time"} ago.
            A "0 fraud" count may just mean the market is not being seen — not that no fraud exists.
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {metrics.map(({ label, value, icon: Icon }) => <Card key={label} className="shadow-sm"><CardContent className="flex items-start justify-between p-5"><div><p className="text-sm text-muted-foreground">{label}</p><p className="mt-2 text-3xl font-semibold tracking-tight">{loading ? "—" : (value ?? 0).toLocaleString()}</p></div><div className="rounded-lg bg-muted p-2.5"><Icon className="h-5 w-5" /></div></CardContent></Card>)}
      </div>
      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="shadow-sm lg:col-span-3"><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Activity className="h-4 w-4" />Last scan</CardTitle><CardDescription>Most recent marketplace monitoring activity</CardDescription></CardHeader><CardContent>{stats?.last_scan ? <div className="grid gap-4 sm:grid-cols-4"><ScanStat label="Source" value={stats.last_scan.source.replaceAll("_", " ")} /><ScanStat label="Listings" value={String(stats.last_scan.listings_found)} /><ScanStat label="Fraud" value={String(stats.last_scan.fraud_found)} /><ScanStat label="Completed" value={formatDate(stats.last_scan.completed_at ?? stats.last_scan.started_at)} /></div> : <div className="rounded-lg bg-muted p-5 text-sm text-muted-foreground">No scans have been run yet.</div>}</CardContent></Card>
        <Card className="shadow-sm lg:col-span-2"><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Settings className="h-4 w-4" />Configuration</CardTitle><CardDescription>{config ? `${config.scrape_city}, ${config.scrape_state}` : "Service readiness"}</CardDescription></CardHeader><CardContent className="space-y-3">{services.map(([name, ready, enabled]) => <div key={name} className="flex items-center justify-between rounded-lg border px-3 py-2.5 text-sm"><span>{name}</span><span className={cn("flex items-center gap-1.5", ready ? (enabled ? "text-green-600" : "text-amber-500") : "text-red-500")}>{ready ? (enabled ? <CheckCircle className="h-4 w-4" /> : <Clock className="h-4 w-4" />) : <XCircle className="h-4 w-4" />}{ready ? (enabled ? "Ready" : "Safe mode") : "Not configured"}</span></div>)}</CardContent></Card>
      </div>
      <Card className="shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><AlertTriangle className="h-4 w-4 text-red-500" />Flagged listings</CardTitle>
          <CardDescription>Listings the detector believes are impersonating your properties. Click one to read the full analysis.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {flagged.length === 0 && <div className="rounded-lg bg-muted p-5 text-sm text-muted-foreground">Nothing flagged right now.</div>}
          {flagged.slice(0, 8).map(listing => {
            const pct = listing.fraud_confidence == null ? null : Math.round(listing.fraud_confidence <= 1 ? listing.fraud_confidence * 100 : listing.fraud_confidence);
            return (
              <button key={listing.id} type="button" onClick={() => setSelected(listing)} className="flex w-full flex-col gap-1 rounded-lg border border-red-500/20 bg-red-500/5 p-3 text-left transition-colors hover:bg-red-500/10">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium">{listing.title}</span>
                  <span className="text-xs text-muted-foreground">{listing.source === "manual" ? "pasted by you" : listing.source.replaceAll("_", " ")}{pct != null && ` · ${pct}%`}{listing.price != null && ` · $${listing.price.toLocaleString()}`}</span>
                </div>
                <span className="line-clamp-2 text-xs text-muted-foreground">{listing.fraud_reason ?? "No reason recorded."}</span>
              </button>
            );
          })}
        </CardContent>
      </Card>
      <ListingDetailDialog listing={selected} properties={properties} onOpenChange={open => { if (!open) setSelected(null); }} />
    </div>
  );
}

function ScanStat({ label, value }: { label: string; value: string }) { return <div><p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p><p className="mt-1 text-sm font-medium capitalize">{value}</p></div>; }
