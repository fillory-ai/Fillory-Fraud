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
  listings_found: number;
  fraud_found: number;
  alerts_sent: number;
  status: string;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface StatsData {
  total_properties: number;
  total_listings_scraped: number;
  fraud_detected: number;
  alerts_sent: number;
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
}
