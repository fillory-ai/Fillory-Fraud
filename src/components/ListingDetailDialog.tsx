import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle, ExternalLink, HelpCircle, MapPin } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { PropertyData, ScrapedListingData } from "@/types";

interface ListingDetailDialogProps {
  listing: ScrapedListingData | null;
  properties: PropertyData[];
  onOpenChange: (open: boolean) => void;
}

const formatDate = (value: string | null | undefined) =>
  value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";

export function ListingDetailDialog({ listing, properties, onOpenChange }: ListingDetailDialogProps) {
  // List responses truncate description to 300 chars; fetch the full record
  // when the dialog opens so the whole listing body is readable.
  const [full, setFull] = useState<ScrapedListingData | null>(null);
  useEffect(() => {
    setFull(null);
    if (!listing) return;
    let cancelled = false;
    void fetch(`/api/listings/${listing.id}`)
      .then(r => (r.ok ? r.json() : null))
      .then(data => { if (!cancelled && data) setFull(data as ScrapedListingData); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [listing]);

  const shown = full ?? listing;
  const matched = shown?.matched_property_id
    ? properties.find(p => p.id === shown.matched_property_id) ?? null
    : null;
  const status = shown?.fraud_status ?? "unknown";
  const Icon = status === "fraud" ? AlertTriangle : status === "legitimate" ? CheckCircle : HelpCircle;
  const pct = shown?.fraud_confidence == null
    ? null
    : Math.round(shown.fraud_confidence <= 1 ? shown.fraud_confidence * 100 : shown.fraud_confidence);

  return (
    <Dialog open={listing !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        {shown && (
          <>
            <DialogHeader>
              <DialogTitle className="pr-8 text-left leading-snug">{shown.title}</DialogTitle>
              <DialogDescription className="text-left">
                {shown.source === "manual" ? "pasted by you (not from a marketplace scan)" : shown.source.replaceAll("_", " ")} · scraped {formatDate(shown.scraped_at)}
              </DialogDescription>
            </DialogHeader>

            <div
              className={cn(
                "rounded-lg border p-4",
                status === "fraud" && "border-red-500/20 bg-red-500/5",
                status === "legitimate" && "border-green-500/20 bg-green-500/5",
                status === "unknown" && "border-muted bg-muted/40",
              )}
            >
              <div className="flex flex-wrap items-center gap-3">
                <Badge
                  variant="outline"
                  className={cn(
                    "gap-1 capitalize",
                    status === "fraud" && "border-red-500/20 bg-red-500/10 text-red-500",
                    status === "legitimate" && "border-green-500/20 bg-green-500/10 text-green-600",
                    status === "unknown" && "border-gray-500/20 bg-gray-500/10 text-gray-500",
                  )}
                >
                  <Icon className="h-3 w-3" />
                  {status}
                </Badge>
                {pct != null && <span className="text-sm text-muted-foreground">{pct}% confidence</span>}
                {shown.alerted_at && (
                  <span className="text-sm text-muted-foreground">alerted {formatDate(shown.alerted_at)}</span>
                )}
              </div>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed">
                {shown.fraud_reason || "No reason recorded."}
              </p>
              <div className="mt-3 text-sm">
                <span className="text-muted-foreground">Matched property: </span>
                {matched ? (
                  <span className="font-medium">
                    {matched.name} — {matched.address}, {matched.city} {matched.state}
                    {matched.monthly_rent != null && ` · listed at $${matched.monthly_rent.toLocaleString()}/mo`}
                  </span>
                ) : (
                  <span>none</span>
                )}
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Price" value={shown.price == null ? "—" : `$${shown.price.toLocaleString()}`} />
              <Field label="Street address" value={shown.street_address || "— (not published by source)"} />
              <Field label="Location" value={shown.location || "—"} />
              <Field label="Posted" value={formatDate(shown.posted_date)} />
            </div>

            {shown.latitude != null && shown.longitude != null && (
              <a
                href={`https://www.google.com/maps/search/?api=1&query=${shown.latitude},${shown.longitude}`}
                target="_blank"
                rel="noreferrer"
                className="inline-flex w-fit items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-muted"
              >
                <MapPin className="h-4 w-4" />
                {shown.latitude.toFixed(5)}, {shown.longitude.toFixed(5)} — view on map
              </a>
            )}

            <div>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">Listing text</p>
              <p className="mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border bg-muted/40 p-3 text-sm leading-relaxed">
                {shown.description || "No description captured."}
              </p>
            </div>

            {shown.image_urls?.length > 0 && (
              <div>
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Images</p>
                <div className="mt-2 flex gap-2 overflow-x-auto">
                  {shown.image_urls.slice(0, 8).map(src => (
                    <img key={src} src={src} alt="" className="h-24 w-32 shrink-0 rounded-md border object-cover" />
                  ))}
                </div>
              </div>
            )}

            {shown.url && (
              <a
                href={shown.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex w-fit items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-muted"
              >
                <ExternalLink className="h-4 w-4" />
                Open original listing
              </a>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-sm font-medium">{value}</p>
    </div>
  );
}
