import { useCallback, useEffect, useState } from "react";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { ListingDetailDialog } from "@/components/ListingDetailDialog";
import { cn } from "@/lib/utils";
import type { CaseData, CaseStatus, PropertyData, ResolutionCode, ScrapedListingData } from "@/types";

interface CasesTabProps {
  cases: CaseData[];
  properties: PropertyData[];
  loading: boolean;
  filter: string;
  onFilterChange: (value: string) => void;
  onUpdateStatus: (caseId: string, status: CaseStatus, resolution?: { code: string; note?: string }) => Promise<void>;
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
  const [resolutionCodes, setResolutionCodes] = useState<ResolutionCode[]>([]);
  const [resolutionDialog, setResolutionDialog] = useState<{ caseId: string; status: CaseStatus } | null>(null);
  const [selectedCode, setSelectedCode] = useState<string>("");
  const [resolutionNote, setResolutionNote] = useState<string>("");
  const [resolutionError, setResolutionError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/cases/resolution-codes")
      .then(r => r.json())
      .then((data: ResolutionCode[]) => { if (!cancelled) setResolutionCodes(data); })
      .catch(() => { /* codes will be empty, dialog shows fallback */ });
    return () => { cancelled = true; };
  }, []);

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

  const openResolutionDialog = useCallback((caseId: string, status: CaseStatus) => {
    setResolutionDialog({ caseId, status });
    setSelectedCode("");
    setResolutionNote("");
    setResolutionError(null);
  }, []);

  const closeResolutionDialog = useCallback(() => {
    setResolutionDialog(null);
    setSelectedCode("");
    setResolutionNote("");
    setResolutionError(null);
  }, []);

  const submitResolution = useCallback(async () => {
    if (!resolutionDialog || !selectedCode) return;
    setSubmitting(true);
    setResolutionError(null);
    try {
      await onUpdateStatus(resolutionDialog.caseId, resolutionDialog.status, {
        code: selectedCode,
        note: resolutionNote.trim() || undefined,
      });
      closeResolutionDialog();
    } catch (err) {
      setResolutionError(err instanceof Error ? err.message : "Failed to update case");
    } finally {
      setSubmitting(false);
    }
  }, [resolutionDialog, selectedCode, resolutionNote, onUpdateStatus, closeResolutionDialog]);

  const resolutionLabel = useCallback(
    (code: string | null) => {
      if (!code) return null;
      return resolutionCodes.find(r => r.code === code)?.label ?? code;
    },
    [resolutionCodes],
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
                  const isClosed = c.status === "resolved" || c.status === "dismissed";
                  const resLabel = resolutionLabel(c.resolution_code);
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
                          {isClosed && resLabel && (
                            <span className="mt-0.5 text-xs text-muted-foreground">
                              <span className="font-medium text-foreground/70">Resolution:</span> {resLabel}
                              {c.resolution_note && <span className="italic"> — {c.resolution_note}</span>}
                            </span>
                          )}
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
                                onClick={() => openResolutionDialog(c.id, "resolved")}
                                className="gap-1"
                              >
                                {updating === c.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle className="h-3.5 w-3.5" />}
                                Resolve
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={updating === c.id}
                                onClick={() => openResolutionDialog(c.id, "dismissed")}
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

      <Dialog open={resolutionDialog !== null} onOpenChange={open => { if (!open) closeResolutionDialog(); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {resolutionDialog?.status === "resolved" ? "Resolve case" : "Dismiss case"}
            </DialogTitle>
            <DialogDescription>
              Select a reason so we can measure precision and tune the detector.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="resolution-code">Reason</Label>
              <Select value={selectedCode} onValueChange={setSelectedCode}>
                <SelectTrigger id="resolution-code">
                  <SelectValue placeholder="Choose a reason…" />
                </SelectTrigger>
                <SelectContent>
                  {resolutionCodes.map(rc => (
                    <SelectItem key={rc.code} value={rc.code}>{rc.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="resolution-note">Note (optional)</Label>
              <Textarea
                id="resolution-note"
                value={resolutionNote}
                onChange={e => setResolutionNote(e.target.value)}
                placeholder="Add context for this decision…"
                rows={3}
              />
            </div>
            {resolutionError && (
              <p className="text-sm text-red-500">{resolutionError}</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeResolutionDialog} disabled={submitting}>
              Cancel
            </Button>
            <Button
              onClick={submitResolution}
              disabled={!selectedCode || submitting}
              className="gap-1"
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {resolutionDialog?.status === "resolved" ? "Resolve" : "Dismiss"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
