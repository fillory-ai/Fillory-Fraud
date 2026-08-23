import { useState } from "react";
import { AlertTriangle, CheckCircle, ExternalLink, Eye, Search, SlidersHorizontal, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ListingDetailDialog } from "@/components/ListingDetailDialog";
import { cn } from "@/lib/utils";
import type { PropertyData, ScrapedListingData } from "@/types";

interface ListingsTabProps {
  listings: ScrapedListingData[];
  properties: PropertyData[];
  filter: string;
  onFilterChange: (value: string) => void;
  onDelete: (listing: ScrapedListingData) => void | Promise<void>;
  loading: boolean;
}

const formatDate = (v: string | null | undefined) =>
  v ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(v)) : "—";

export function ListingsTab({ listings, properties, filter, onFilterChange, onDelete, loading }: ListingsTabProps) {
  const [selected, setSelected] = useState<ScrapedListingData | null>(null);
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Listings</h1>
          <p className="mt-1 text-sm text-muted-foreground">Review scraped rental listings and detection results. Click any row to read the full analysis.</p>
        </div>
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
          <Select value={filter} onValueChange={onFilterChange}>
            <SelectTrigger className="w-[180px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="fraud">Fraud</SelectItem>
              <SelectItem value="legitimate">Legitimate</SelectItem>
              <SelectItem value="unknown">Unknown</SelectItem>
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
                  <TableHead>Source</TableHead>
                  <TableHead className="min-w-[220px]">Listing</TableHead>
                  <TableHead>Price</TableHead>
                  <TableHead>Location</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="min-w-[140px]">Confidence</TableHead>
                  <TableHead className="min-w-[220px]">Reason</TableHead>
                  <TableHead className="text-right">Link</TableHead>
                  <TableHead className="w-[52px]"><span className="sr-only">Delete</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {listings.map(listing => (
                  <TableRow key={listing.id} onClick={() => setSelected(listing)} className="cursor-pointer">
                    <TableCell><SourceBadge source={listing.source} /></TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <span className="font-medium">{listing.title}</span>
                        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                          {listing.times_seen != null && listing.times_seen > 1 && (
                            <span className="inline-flex items-center gap-0.5">
                              <Eye className="h-3 w-3" />seen {listing.times_seen}x
                            </span>
                          )}
                          {listing.first_seen_at && <span>first {formatDate(listing.first_seen_at)}</span>}
                          {listing.delisted_at && (
                            <Badge variant="outline" className="border-green-500/20 bg-green-500/10 text-green-600">delisted</Badge>
                          )}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>{listing.price == null ? "—" : `$${listing.price.toLocaleString()}`}</TableCell>
                    <TableCell className="text-muted-foreground">{listing.location ?? "—"}</TableCell>
                    <TableCell><FraudBadge status={listing.fraud_status} /></TableCell>
                    <TableCell><Confidence value={listing.fraud_confidence} /></TableCell>
                    <TableCell className="max-w-[320px]"><span className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">{listing.fraud_reason ?? "—"}</span></TableCell>
                    <TableCell className="text-right">
                      <a href={listing.url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} aria-label={`Open ${listing.title}`} className="inline-flex rounded-md p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
                        <ExternalLink className="h-4 w-4" />
                      </a>
                    </TableCell>
                    <TableCell className="text-right">
                      <button type="button" onClick={e => { e.stopPropagation(); if (window.confirm(`Delete "${listing.title}"?`)) void onDelete(listing); }} aria-label={`Delete ${listing.title}`} className="inline-flex rounded-md p-2 text-muted-foreground transition-colors hover:bg-red-500/10 hover:text-red-500">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {!loading && listings.length === 0 && <Empty icon={Search} text="No listings match this filter." />}
          {loading && <div className="p-10 text-center text-sm text-muted-foreground">Loading listings…</div>}
        </CardContent>
      </Card>
      <ListingDetailDialog listing={selected} properties={properties} onOpenChange={open => { if (!open) setSelected(null); }} />
    </div>
  );
}

function SourceBadge({ source }: { source: string }) {
  const facebook = source === "facebook_marketplace";
  const manual = source === "manual";
  return <Badge variant="outline" className={cn(manual ? "border-amber-500/20 bg-amber-500/10 text-amber-600" : facebook ? "border-indigo-500/20 bg-indigo-500/10 text-indigo-600" : "border-blue-500/20 bg-blue-500/10 text-blue-600")}>{manual ? "pasted by you" : source.replaceAll("_", " ")}</Badge>;
}

function FraudBadge({ status }: { status: ScrapedListingData["fraud_status"] }) {
  const Icon = status === "fraud" ? AlertTriangle : status === "legitimate" ? CheckCircle : null;
  return <Badge variant="outline" className={cn("gap-1 capitalize", status === "fraud" && "bg-red-500/10 text-red-500 border-red-500/20", status === "legitimate" && "bg-green-500/10 text-green-500 border-green-500/20", status === "unknown" && "bg-gray-500/10 text-gray-500 border-gray-500/20")}>{Icon && <Icon className="h-3 w-3" />}{status}</Badge>;
}

function Confidence({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const pct = value <= 1 ? Math.round(value * 100) : Math.round(value);
  return <div className="flex items-center gap-2"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, pct)}%` }} /></div><span className="w-9 text-xs text-muted-foreground">{pct}%</span></div>;
}

function Empty({ icon: Icon, text }: { icon: typeof Search; text: string }) {
  return <div className="flex flex-col items-center gap-2 p-12 text-sm text-muted-foreground"><Icon className="h-7 w-7" /><span>{text}</span></div>;
}
