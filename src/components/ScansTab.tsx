import { AlertTriangle, CheckCircle, Clock, RefreshCw, ScanLine, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { ScanHealthResponse, ScanLogData } from "@/types";

interface ScansTabProps {
  scans: ScanLogData[];
  source: string;
  onSourceChange: (value: string) => void;
  onRunScan: () => void;
  scanning: boolean;
  health: ScanHealthResponse | null;
}

const date = (v: string | null) => v ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(v)) : "—";

export function ScansTab({ scans, source, onSourceChange, onRunScan, scanning, health }: ScansTabProps) {
  const scheduler = health?.scheduler;
  const nextRun = scheduler?.jobs?.[0]?.next_run_at ?? null;
  const lastCheck = scheduler?.last_health_check;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Scans</h1>
        <p className="mt-1 text-sm text-muted-foreground">Search marketplaces for unauthorized rental listings.</p>
      </div>

      <Card className="shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><ScanLine className="h-4 w-4" />Run a marketplace scan</CardTitle>
          <CardDescription>Select a source and begin monitoring. This may take a few minutes.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 sm:flex-row">
          <Select value={source} onValueChange={onSourceChange}>
            <SelectTrigger className="sm:w-[240px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All sources</SelectItem>
              <SelectItem value="craigslist">Craigslist</SelectItem>
              <SelectItem value="facebook_marketplace">Facebook Marketplace</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={onRunScan} disabled={scanning} className="gap-2">
            <RefreshCw className={cn("h-4 w-4", scanning && "animate-spin")} />
            {scanning ? "Scanning…" : "Run Scan"}
          </Button>
        </CardContent>
      </Card>

      {scheduler && (
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base"><Clock className="h-4 w-4" />Scheduler</CardTitle>
            <CardDescription>{scheduler.enabled ? "Automated scanning is active." : "Automated scanning is off — scanning is manual-only right now."}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-4">
              <SchedStat label="Status" value={scheduler.enabled ? "Enabled" : "Disabled"} enabled={scheduler.enabled} />
              <SchedStat label="Interval" value={scheduler.enabled ? `${scheduler.interval_hours}h` : "—"} />
              <SchedStat label="Next run" value={scheduler.enabled ? date(nextRun) : "—"} />
              <SchedStat label="Last health check" value={lastCheck ? date(lastCheck.checked_at) : "—"} />
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden shadow-sm">
        <CardHeader><CardTitle className="text-base">Scan history</CardTitle></CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Listings</TableHead>
                  <TableHead>New</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead>Cases</TableHead>
                  <TableHead>Enrichment</TableHead>
                  <TableHead>Fraud</TableHead>
                  <TableHead>Alerts</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Completed</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {scans.map(scan => (
                  <TableRow key={scan.id}>
                    <TableCell className="font-medium capitalize">{scan.source.replaceAll("_", " ")}</TableCell>
                    <TableCell className="text-muted-foreground capitalize">{scan.trigger ?? "—"}</TableCell>
                    <TableCell><Status status={scan.status} /></TableCell>
                    <TableCell>{scan.listings_found}</TableCell>
                    <TableCell className="text-muted-foreground">{scan.listings_new ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{scan.listings_updated ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{scan.cases_opened ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground">{scan.enrichment_rate != null ? `${Math.round(scan.enrichment_rate * 100)}%` : "—"}</TableCell>
                    <TableCell>{scan.fraud_found}</TableCell>
                    <TableCell>{scan.alerts_sent}</TableCell>
                    <TableCell className="text-muted-foreground">{date(scan.started_at)}</TableCell>
                    <TableCell className="text-muted-foreground">{date(scan.completed_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {scans.length === 0 && <div className="p-12 text-center text-sm text-muted-foreground">No scan history yet.</div>}
        </CardContent>
      </Card>
    </div>
  );
}

function Status({ status }: { status: string }) {
  const good = ["completed", "success"].includes(status.toLowerCase());
  const bad = ["failed", "error"].includes(status.toLowerCase());
  const Icon = good ? CheckCircle : bad ? XCircle : AlertTriangle;
  return <Badge variant="outline" className={cn("gap-1 capitalize", good && "border-green-500/20 bg-green-500/10 text-green-600", bad && "border-red-500/20 bg-red-500/10 text-red-500", !good && !bad && "border-amber-500/20 bg-amber-500/10 text-amber-600")}><Icon className="h-3 w-3" />{status}</Badge>;
}

function SchedStat({ label, value, enabled }: { label: string; value: string; enabled?: boolean }) {
  return <div><p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p><p className={cn("mt-1 text-sm font-medium", enabled != null && (enabled ? "text-green-600" : "text-muted-foreground"))}>{value}</p></div>;
}
