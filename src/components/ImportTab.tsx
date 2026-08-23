import { useState } from "react";
import { AlertTriangle, CheckCircle, ClipboardPaste, ExternalLink, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { ImportResultData } from "@/types";

const EXAMPLE = `3 Bedroom Spacious TownHome | Fenced Backyard | Outdoor Patio | Granite Countertops
4411 NE Killingsworth St Unit 107, Portland, OR 97218
3 beds, 1.5 baths — Available 9/17/2026 — $2995
https://portland.craigslist.org/mlt/apa/d/example/1234567890.html`;

interface ImportTabProps {
  onImported: () => Promise<void> | void;
}

export function ImportTab({ onImported }: ImportTabProps) {
  const [text, setText] = useState("");
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResultData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canImport = text.trim().length >= 20 && !importing;

  const importListing = async () => {
    setImporting(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch("/api/listings/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) throw new Error((await response.json()).detail ?? "Import failed");
      setResult(await response.json() as ImportResultData);
      await onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setImporting(false);
    }
  };

  const analysis = result?.analysis;
  const listing = result?.listing;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Import</h1>
        <p className="mt-1 text-sm text-muted-foreground">Paste a listing you found anywhere — it gets parsed and checked against your properties automatically.</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base"><ClipboardPaste className="h-4 w-4" />Paste listing text</CardTitle>
            <CardDescription>Copy the whole listing — title, price, address, description, link. The messier, the better; the parser figures it out.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              value={text}
              onChange={e => setText(e.target.value)}
              placeholder={EXAMPLE}
              className="min-h-[220px] font-mono text-xs leading-relaxed"
              aria-label="Pasted listing text"
            />
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">{text.trim().length} characters</span>
              <Button onClick={importListing} disabled={!canImport}>
                {importing ? <><Loader2 className="h-4 w-4 animate-spin" />Analyzing…</> : <>Analyze listing</>}
              </Button>
            </div>
            {error && <p className="text-sm text-red-500">{error}</p>}
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-base">Result</CardTitle>
            <CardDescription>{analysis ? "Parsed fields and fraud assessment" : "Nothing imported yet"}</CardDescription>
          </CardHeader>
          <CardContent>
            {!analysis && <div className="rounded-lg bg-muted p-5 text-sm text-muted-foreground">Paste a listing on the left and hit <span className="font-medium text-foreground">Analyze listing</span>.</div>}
            {analysis && listing && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <StatusBadge status={analysis.fraud_status} />
                  {analysis.confidence != null && <span className="text-xs text-muted-foreground">confidence {Math.round(analysis.confidence * 100)}%</span>}
                </div>
                <dl className="space-y-2 text-sm">
                  <Row label="Title" value={listing.title} />
                  <Row label="Price" value={listing.price == null ? "—" : `$${listing.price.toLocaleString()}`} />
                  <Row label="Location" value={listing.location || "—"} />
                  <Row label="Source" value={<Badge variant="outline" className="border-blue-500/20 bg-blue-500/10 text-blue-600">{listing.source.replaceAll("_", " ")}</Badge>} />
                  {listing.url && <Row label="Link" value={<a href={listing.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">{listing.url.slice(0, 40)}<ExternalLink className="h-3 w-3" /></a>} />}
                </dl>
                <div className="rounded-lg border bg-muted/40 p-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Assessment</p>
                  <p className="mt-1 text-sm leading-relaxed">{analysis.reason}</p>
                </div>
                {analysis.alert_status && (
                  <p className="text-xs text-muted-foreground">
                    Alert: <span className={cn("font-medium", analysis.alert_sent ? "text-green-600" : "text-amber-600")}>{analysis.alert_status}</span>
                    {analysis.alert_status === "skipped" && " — SMS is in safe mode (recorded, not sent)"}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: "fraud" | "legitimate" | "unknown" }) {
  const Icon = status === "fraud" ? AlertTriangle : status === "legitimate" ? CheckCircle : null;
  return <Badge variant="outline" className={cn("gap-1 capitalize", status === "fraud" && "bg-red-500/10 text-red-500 border-red-500/20", status === "legitimate" && "bg-green-500/10 text-green-500 border-green-500/20", status === "unknown" && "bg-gray-500/10 text-gray-500 border-gray-500/20")}>{Icon && <Icon className="h-3 w-3" />}{status}</Badge>;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="flex items-start justify-between gap-4"><dt className="shrink-0 text-xs uppercase tracking-wide text-muted-foreground">{label}</dt><dd className="min-w-0 break-words text-right font-medium">{value}</dd></div>;
}
