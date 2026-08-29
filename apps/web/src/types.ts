export type Source = "LEDGER" | "COUNTERPARTY";
export type Health = {
  status: string;
  implementation: "python";
  version: string;
};
export type Ingestion = {
  id: number;
  source: Source;
  filename: string;
  checksum: string;
  row_count: number;
  changed_count: number;
  duplicate?: boolean;
  created_at: string;
  closed_at: string | null;
  closed_by: string | null;
};
export type Summary = Record<string, number>;
export type Run = {
  id: number;
  status: string;
  summary: Summary;
  created_at: string;
};
export type Transaction = {
  id: number;
  version_id: number;
  source: Source;
  external_id: string;
  executed_at: string;
  instrument: string;
  side: string;
  quantity: string;
  price: string;
  gross_amount: string;
  state: string;
  raw: Record<string, string>;
};
export type Difference = {
  field: string;
  left: string;
  right: string;
  absolute_delta: string | null;
  relative_delta: string | null;
  tolerance: string;
  passed: boolean;
};
export type ResultItem = {
  id: number;
  status: string;
  match_method: string;
  score: string | null;
  review_status: "NOT_REQUIRED" | "PENDING" | "ACCEPTED" | "RESOLVED";
  resolution_type: string | null;
  ledger: Transaction | null;
  counterparty: Transaction | null;
  differences: Difference[];
};
export type Results = {
  items: ResultItem[];
  total: number;
  page: number;
  page_size: number;
  summary: Summary;
};
export type Audit = {
  id: number;
  action: string;
  entity_type: string;
  entity_id: string;
  actor: string;
  details: Record<string, unknown>;
  created_at: string;
};
