export type ApiStatus = "checking" | "connected" | "error";

export interface HealthResponse {
  ok: boolean;
}

export interface PropertyData {
  id: string;
  name: string;
  address: string;
  city: string;
  state: string;
  zip_code: string | null;
  zip_plus4: string | null;
  bedrooms: number | null;
  bathrooms: number | null;
  square_footage: number | null;
  monthly_rent: number | null;
  description: string | null;
  image_urls: string[];
  amenities: string[];
  created_at: string;
}

export interface ScrapedListingData {
  id: string;
  source: string;
  external_id: string | null;
  title: string;
  price: number | null;
  location: string | null;
  description: string | null;
  url: string;
  image_urls: string[];
  posted_date: string | null;
  street_address: string | null;
  latitude: number | null;
  longitude: number | null;
  enriched: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
  times_seen: number | null;
  delisted_at: string | null;
  fraud_status: "fraud" | "legitimate" | "unknown";
  fraud_confidence: number | null;
  fraud_reason: string | null;
  matched_property_id: string | null;
  alerted_at: string | null;
  scraped_at: string;
}

export interface ImportAnalysisData {
  listing_id: string;
  fraud_status: "fraud" | "legitimate" | "unknown";
  confidence: number;
  reason: string;
  matched_property_id: string | null;
  alert_status: string | null;
  alert_sent: boolean;
}

export interface ImportResultData {
  listing: ScrapedListingData | null;
  analysis: ImportAnalysisData;
}

export interface AlertData {
  id: string;
  listing_id: string;
  property_id: string | null;
  alert_type: string;
  recipient: string;
  message: string;
  sent_at: string | null;
  status: string;
  error_message: string | null;
  created_at: string;
}

export interface ScanLogData {
  id: string;
  source: string;
  trigger: string | null;
  listings_found: number;
  listings_new: number | null;
  listings_updated: number | null;
  cases_opened: number | null;
  enrichment_rate: number | null;
  fraud_found: number;
  alerts_sent: number;
  status: string;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface ScanHealthData {
  healthy: boolean;
  reason: string;
  last_success_at: string | null;
  last_scan_status: string | null;
  hours_since_success: number | null;
  stale_after_hours: number;
  enrichment_rate: number | null;
}

export interface SchedulerJobData {
  id: string;
  next_run_at: string | null;
}

export interface SchedulerData {
  enabled: boolean;
  running: boolean;
  interval_hours: number;
  jitter_minutes: number;
  jobs: SchedulerJobData[];
  last_health_check: (ScanHealthData & { checked_at: string }) | null;
}

export interface ScanHealthResponse extends ScanHealthData {
  scheduler: SchedulerData;
}

export type CaseStatus = "open" | "acknowledged" | "filed" | "resolved" | "dismissed" | "disputed";

export interface ResolutionCode {
  code: string;
  label: string;
}

export interface CaseData {
  id: string;
  listing_id: string;
  property_id: string | null;
  status: CaseStatus;
  confidence: number | null;
  reason: string | null;
  match_signal: string | null;
  opened_at: string;
  updated_at: string;
  last_alert_at: string | null;
  alert_count: number;
  // Newline-separated free text appended by the pipeline, not structured events.
  change_log: string | null;
  resolved_at: string | null;
  alerts_recorded: number;
  resolution_code: string | null;
  resolution_note: string | null;
  listing: ScrapedListingData | null;
  property_name: string | null;
}

export interface StatsData {
  total_properties: number;
  total_listings_scraped: number;
  fraud_detected: number;
  alerts_sent: number;
  open_cases: number;
  observe_mode: boolean;
  scan_health: ScanHealthData | null;
  last_scan: ScanLogData | null;
}

export interface ConfigStatusData {
  apify_configured: boolean;
  twilio_configured: boolean;
  twilio_enabled: boolean;
  gemini_configured: boolean;
  scrape_city: string;
  scrape_state: string;
  alert_phone: string;
  observe_mode: boolean;
  scheduler_enabled: boolean;
  scan_interval_hours: number;
}
