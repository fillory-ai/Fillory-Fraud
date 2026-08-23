import { useCallback, useState } from "react";
import {
  AlertTriangle,
  CheckCircle,
  ExternalLink,
  Eye,
  FolderCheck,
  Loader2,
  MapPin,
  ShieldOff,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ListingDetailDialog } from "@/components/ListingDetailDialog";
import { cn } from "@/lib/utils";
import type { CaseData, CaseStatus, PropertyData, ScrapedListingData } from "@/types";

interface CasesTabProps {
  cases: CaseData[];
  properties: PropertyData[];
  loading: boolean;
  filter: string;
  onFilterChange: (value: string) => void;
  onUpdateStatus: (caseId: string, status: CaseStatus) => Promise<void>;
}

const formatDate = (v: string | null | undefined) =>
  v ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(v)) : "—";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "all", label: "All cases" },
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "filed", label: "Filed" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
  { value: "disputed", label: "Disputed" },
];

export function CasesTab({ cases, properties, loading, filter, onFilterChange, onUpdateStatus }: CasesTabProps) {
  const [selected, setSelected] = useState<ScrapedListingData | null>(null);
  const [updating, setUpdating] = useState<string | null>(null);

  const handleStatus = useCallback(
    async (caseId: string, status: CaseStatus) => {
      setUpdating(caseId);
      try {
        await onUpdateStatus(caseId, status);
      } finally {
        setUpdating(null);
      }
    },
    [onUpdateStatus],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cases</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Review queue for listings that matched a registered property. Open a case to read the full listing.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={filter} onValueChange={onFilterChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map(o => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Card className="overflow-hidden shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="min-w-[200px]">Listing</TableHead>
                  <TableHead>Property</TableHead>
                  <TableHead>Signal</TableHead>
                  <TableHead className="min-w-[120px]">Confidence</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Alerts</TableHead>
                  <TableHead>Opened</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {cases.map(c => {
                  const listing = c.listing;
                  const pct = c.confidence == null ? null : Math.round(c.confidence <= 1 ? c.confidence * 100 : c.confidence);
                  return (
                    <TableRow
                      key={c.id}
                      onClick={() => listing && setSelected(listing)}
                      className={cn(listing && "cursor-pointer")}
                    >
                      <TableCell>
                        <div className="flex flex-col gap-0.5">
                          <span className="font-medium">{listing?.title ?? "Listing removed"}</span>
                          <span className="text-xs text-muted-foreground">
                            {listing ? (
                              <>
                                {listing.source === "manual" ? "pasted by you" : listing.source.replaceAll("_", " ")}
                                {listing.price != null && ` · $${listing.price.toLocaleString()}`}
                                {listing.times_seen != null && listing.times_seen > 1 && ` · seen ${listing.times_seen}x`}
                              </>
                            ) : "—"}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{c.property_name ?? "—"}</TableCell>
                      <TableCell>
                        {c.match_signal ? (
                          <Badge variant="outline" className="gap-1 capitalize border-blue-500/20 bg-blue-500/10 text-blue-600">
                            <MapPin className="h-3 w-3" />
                            {c.match_signal.replaceAll("_", " ")}
                          </Badge>
                        ) : <span className="text-muted-foreground">—</span>}
                      </TableCell>
                      <TableCell>
                        {pct == null ? <span className="text-muted-foreground">—</span> : (
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                              <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, pct)}%` }} />
                            </div>
                            <span className="w-9 text-xs text-muted-foreground">{pct}%</span>
                          </div>
                        )}
                      </TableCell>
                      <TableCell><CaseStatusBadge status={c.status} /></TableCell>
                      <TableCell className="text-muted-foreground">{c.alert_count}</TableCell>
                      <TableCell className="text-muted-foreground">{formatDate(c.opened_at)}</TableCell>
                      <TableCell className="text-right" onClick={e => e.stopPropagation()}>
                        <div className="flex justify-end gap-1">
                          {c.status === "open" && (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={updating === c.id}
                              onClick={() => handleStatus(c.id, "acknowledged")}
                              className="gap-1"
                            >
                              {updating === c.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
                              Acknowledge
                            </Button>
                          )}
                          {(c.status === "open" || c.status === "acknowledged") && (
                            <>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={updating === c.id}
                                onClick={() => handleStatus(c.id, "resolved")}
                                className="gap-1"
                              >
                                {updating === c.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle className="h-3.5 w-3.5" />}
                                Resolve
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={updating === c.id}
                                onClick={() => handleStatus(c.id, "dismissed")}
                                className="gap-1"
                              >
                                {updating === c.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldOff className="h-3.5 w-3.5" />}
                                Dismiss
                              </Button>
                            </>
                          )}
                          {listing?.url && (
                            <a
                              href={listing.url}
                              target="_blank"
                              rel="noreferrer"
                              aria-label="Open original listing"
                              className="inline-flex items-center rounded-md border p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          {!loading && cases.length === 0 && (
            <div className="flex flex-col items-center gap-3 p-16 text-center">
              <div className="rounded-full bg-green-500/10 p-3">
                <CheckCircle className="h-8 w-8 text-green-500" />
              </div>
              <div>
                <p className="text-sm font-medium">No open cases</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Nothing is currently impersonating a registered property. New matches from scans will appear here.
                </p>
              </div>
            </div>
          )}
          {loading && <div className="p-10 text-center text-sm text-muted-foreground">Loading cases…</div>}
        </CardContent>
      </Card>

      <ListingDetailDialog listing={selected} properties={properties} onOpenChange={open => { if (!open) setSelected(null); }} />
    </div>
  );
}

function CaseStatusBadge({ status }: { status: CaseStatus }) {
  const styles: Record<CaseStatus, string> = {
    open: "border-red-500/20 bg-red-500/10 text-red-500",
    acknowledged: "border-amber-500/20 bg-amber-500/10 text-amber-600",
    filed: "border-blue-500/20 bg-blue-500/10 text-blue-600",
    resolved: "border-green-500/20 bg-green-500/10 text-green-600",
    dismissed: "border-gray-500/20 bg-gray-500/10 text-gray-500",
    disputed: "border-purple-500/20 bg-purple-500/10 text-purple-600",
  };
  const Icon = status === "open" ? AlertTriangle
    : status === "acknowledged" ? Eye
    : status === "filed" ? FolderCheck
    : status === "resolved" ? CheckCircle
    : status === "dismissed" ? XCircle
    : AlertTriangle;
  return (
    <Badge variant="outline" className={cn("gap-1 capitalize", styles[status])}>
      <Icon className="h-3 w-3" />
      {status}
    </Badge>
  );
}
