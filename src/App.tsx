import { useCallback, useEffect, useState } from "react";
import { AlertsTab } from "@/components/AlertsTab";
import { CasesTab } from "@/components/CasesTab";
import { DashboardTab } from "@/components/DashboardTab";
import { ImportTab } from "@/components/ImportTab";
import { ListingsTab } from "@/components/ListingsTab";
import { PropertiesTab } from "@/components/PropertiesTab";
import type { PropertyFormValues } from "@/components/PropertiesTab";
import { ScansTab } from "@/components/ScansTab";
import { TopNav } from "@/components/TopNav";
import type { TabId } from "@/components/TopNav";
import type { AlertData, CaseData, CaseStatus, ConfigStatusData, PropertyData, ScanHealthResponse, ScanLogData, ScrapedListingData, StatsData } from "./types";

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    try { const json = JSON.parse(body); if (json.detail) message = json.detail; } catch { /* not JSON */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
function App() {
  const [tab, setTab] = useState<TabId>("dashboard"); const [stats, setStats] = useState<StatsData | null>(null); const [config, setConfig] = useState<ConfigStatusData | null>(null); const [properties, setProperties] = useState<PropertyData[]>([]); const [listings, setListings] = useState<ScrapedListingData[]>([]); const [scans, setScans] = useState<ScanLogData[]>([]); const [alerts, setAlerts] = useState<AlertData[]>([]); const [fraudListings, setFraudListings] = useState<ScrapedListingData[]>([]); const [cases, setCases] = useState<CaseData[]>([]); const [scanHealth, setScanHealth] = useState<ScanHealthResponse | null>(null); const [loading, setLoading] = useState(true); const [filter, setFilter] = useState("all"); const [caseFilter, setCaseFilter] = useState("all"); const [source, setSource] = useState("all"); const [scanning, setScanning] = useState(false); const [dialogOpen, setDialogOpen] = useState(false); const [editing, setEditing] = useState<PropertyData | null>(null); const [saving, setSaving] = useState(false);
  const refresh = useCallback(async () => { setLoading(true); try { const [s, c, p, sh, a, f, h] = await Promise.all([api<StatsData>("/api/stats"), api<ConfigStatusData>("/api/config/status"), api<PropertyData[]>("/api/properties"), api<ScanLogData[]>("/api/scans"), api<AlertData[]>("/api/alerts"), api<ScrapedListingData[]>("/api/listings?fraud_status=fraud&limit=50"), api<ScanHealthResponse>("/api/scans/health")]); setStats(s); setConfig(c); setProperties(p); setScans(sh); setAlerts(a); setFraudListings(f); setScanHealth(h); } finally { setLoading(false); } }, []);
  const loadListings = useCallback(async (status: string) => { setLoading(true); try { setListings(await api<ScrapedListingData[]>(`/api/listings?${status === "all" ? "" : `fraud_status=${status}&`}limit=100`)); } finally { setLoading(false); } }, []);
  const loadCases = useCallback(async (status: string) => { setLoading(true); try { setCases(await api<CaseData[]>(`/api/cases?${status === "all" ? "" : `status=${status}&`}limit=100`)); } finally { setLoading(false); } }, []);
  useEffect(() => { void refresh(); }, [refresh]); useEffect(() => { void loadListings(filter); }, [filter, loadListings]); useEffect(() => { if (tab === "cases") void loadCases(caseFilter); }, [tab, caseFilter, loadCases]);
  const runScan = async () => { setScanning(true); try { await api(`/api/scan?source=${source}`, { method: "POST" }); await refresh(); await loadListings(filter); } finally { setScanning(false); } };
  const updateCaseStatus = async (caseId: string, status: CaseStatus, resolution?: { code: string; note?: string }) => { const updated = await api<CaseData>(`/api/cases/${caseId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status, resolution_code: resolution?.code, resolution_note: resolution?.note }) }); setCases(prev => prev.map(c => c.id === caseId ? updated : c)); };
  const saveProperty = async (values: PropertyFormValues) => { setSaving(true); const payload = { ...values, zip_code: values.zip_code || null, zip_plus4: values.zip_plus4 || null, bedrooms: values.bedrooms ?? null, bathrooms: values.bathrooms ?? null, square_footage: values.square_footage ?? null, monthly_rent: values.monthly_rent ?? null, description: values.description || null, amenities: values.amenities.split(",").map(v => v.trim()).filter(Boolean) }; try { await api(editing ? `/api/properties/${editing.id}` : "/api/properties", { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); setDialogOpen(false); await refresh(); } finally { setSaving(false); } };
  const deleteProperty = async (property: PropertyData) => { if (!window.confirm(`Delete ${property.name}?`)) return; await api(`/api/properties/${property.id}`, { method: "DELETE" }); await refresh(); };
  const deleteListing = async (listing: ScrapedListingData) => { await api(`/api/listings/${listing.id}`, { method: "DELETE" }); await refresh(); await loadListings(filter); };
  const refreshAfterImport = useCallback(async () => { await refresh(); await loadListings(filter); }, [refresh, loadListings, filter]);
  return <div className="min-h-screen bg-muted/30"><TopNav activeTab={tab} onTabChange={setTab} observeMode={config?.observe_mode} openCases={stats?.open_cases} /><main className="mx-auto max-w-7xl px-4 py-8 md:px-6">{tab === "dashboard" && <DashboardTab stats={stats} config={config} listings={fraudListings} properties={properties} loading={loading} />}{tab === "import" && <ImportTab onImported={refreshAfterImport} />}{tab === "properties" && <PropertiesTab properties={properties} open={dialogOpen} editing={editing} saving={saving} onOpenChange={setDialogOpen} onAdd={() => { setEditing(null); setDialogOpen(true); }} onEdit={p => { setEditing(p); setDialogOpen(true); }} onDelete={deleteProperty} onSave={saveProperty} />}{tab === "listings" && <ListingsTab listings={listings} properties={properties} filter={filter} onFilterChange={setFilter} onDelete={deleteListing} loading={loading} />}{tab === "cases" && <CasesTab cases={cases} properties={properties} loading={loading} filter={caseFilter} onFilterChange={setCaseFilter} onUpdateStatus={updateCaseStatus} />}{tab === "scans" && <ScansTab scans={scans} source={source} onSourceChange={setSource} onRunScan={runScan} scanning={scanning} health={scanHealth} />}{tab === "alerts" && <AlertsTab alerts={alerts} />}</main></div>;
}
export default App;
