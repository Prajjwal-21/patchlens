/**
 * Wire types for the PatchLens API.
 *
 * These mirror the Pydantic response models. Optional and nullable fields are
 * intentionally explicit: the UI must distinguish "not reported" (`null`) from
 * zero, especially for evaluation metrics.
 */

export interface Pagination {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ProviderSummary {
  provider: string;
  model: string;
  base_url?: string | null;
  api_key_configured: boolean;
  is_mock: boolean;
  is_neural?: boolean;
  dim?: number;
  timeout_seconds?: number;
  max_output_tokens?: number;
}

export interface RetrievalSummary {
  strategy: string;
  top_k: number;
  candidate_k: number;
  rrf_k: number;
  reranker: string;
  semantic_backend: string;
  pgvector_available?: boolean;
  max_results_per_file?: number;
  note?: string;
}

export interface ReviewRule {
  rule_id: string;
  category: string;
  severity: string;
  verification_status: string;
  detector_confidence: number;
  summary: string;
  remediation: string;
}

export interface Meta {
  app_name: string;
  version: string;
  environment: string;
  demo_mode: boolean;
  llm: ProviderSummary;
  embedding: ProviderSummary;
  retrieval: RetrievalSummary;
  capabilities: {
    github_app: boolean;
    github_publishing: boolean;
    test_execution: string;
    local_repository_registration: boolean;
    auth_required: boolean;
  };
  review_rules: ReviewRule[];
  retrieval_strategies: string[];
  feedback_labels: string[];
}

export interface RepositoryVersion {
  id: string;
  commit_sha: string;
  branch: string | null;
  status: string;
  file_count: number;
  symbol_count: number;
  test_count: number;
  embedding_count: number;
  skipped_file_count: number;
  index_duration_ms: number | null;
  embedding_provider: string | null;
  embedding_model: string | null;
  embedding_dim: number | null;
  indexed_at: string | null;
  error: string | null;
}

export interface Repository {
  id: string;
  provider: string;
  owner: string;
  name: string;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  language_primary: string;
  index_status: string;
  indexed_commit_sha: string | null;
  last_indexed_at: string | null;
  last_index_error: string | null;
  latest_version: RepositoryVersion | null;
}

export interface RepositoryDetail {
  repository: Repository;
  versions: RepositoryVersion[];
  recent_reviews: Review[];
  finding_counts: Record<string, number>;
}

export interface CodeSymbol {
  id: string;
  path: string;
  language: string;
  kind: string;
  name: string;
  qualified_name: string;
  parent_qualified_name: string | null;
  signature: string | null;
  docstring: string | null;
  start_line: number;
  end_line: number;
  is_test: boolean;
  source_truncated: boolean;
}

export interface CodeSymbolListResponse {
  items: CodeSymbol[];
  pagination: Pagination;
}

export interface Review {
  id: string;
  repository_id: string;
  repository_full_name: string | null;
  pull_request_id: string | null;
  commit_sha: string;
  base_sha: string | null;
  status: string;
  stage: string;
  progress: number;
  strategy: string;
  llm_provider: string;
  llm_model: string;
  is_mock_provider: boolean;
  trigger: string;
  changed_files: number;
  changed_lines: number;
  retrieved_context_count: number;
  finding_count: number;
  verified_finding_count: number;
  candidate_test_count: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  duration_ms: number | null;
  error: string | null;
  usage: Record<string, unknown>;
  job_id: string | null;
  metadata: Record<string, unknown> | null;
  publish_state: string;
  publish_detail: string | null;
  published_comment_url: string | null;
  published_at: string | null;
}

export interface ReviewDetail {
  review: Review;
  job: Job | null;
  finding_summary: {
    by_severity: Record<string, number>;
    by_verification_status: Record<string, number>;
    by_category: Record<string, number>;
  };
}

export interface ReviewCreateResponse {
  review: Review;
  job_id: string | null;
  executed_synchronously: boolean;
  note: string | null;
}

export interface Evidence {
  kind: string;
  detail: Record<string, unknown>;
}

export interface FindingFeedback {
  id: string;
  label: string;
  comment: string | null;
  reviewer: string;
  source: string;
  created_at: string;
}

export interface Finding {
  id: string;
  review_id: string;
  repository_id: string;
  path: string;
  start_line: number;
  end_line: number;
  line_verified: boolean;
  rule_id: string;
  category: string;
  severity: string;
  title: string;
  explanation: string;
  suggestion: string | null;
  suggested_test: string | null;
  verification_status: string;
  confidence: number | null;
  confidence_basis: string;
  detector: string;
  provider: string | null;
  model: string | null;
  evidence: Evidence[];
  retrieval_evidence: Evidence[];
  rejection_reason: string | null;
  publish_state: string;
  publish_detail: string | null;
  published_comment_url: string | null;
  created_at: string;
  feedback: FindingFeedback[];
}

export interface FindingListResponse {
  items: Finding[];
  pagination: Pagination;
}

export interface FindingTrace {
  finding_id: string;
  rule_id: string;
  rule: ReviewRule | null;
  rule_is_deterministic: boolean;
  detector: string;
  verification_status: string;
  confidence: number | null;
  confidence_basis: string;
  rejection_reason: string | null;
  dedupe_of_finding_id: string | null;
  evidence: Evidence[];
  retrieval_evidence: Evidence[];
  provider: string | null;
  model: string | null;
  path: string;
  start_line: number;
  end_line: number;
  line_verified: boolean;
  not_executed: string[];
  note: string;
}

export interface FeedbackAggregate {
  total_findings: number;
  labelled_findings: number;
  labels: Record<string, number>;
  by_category: Record<string, Record<string, number>>;
  by_verification_status: Record<string, Record<string, number>>;
  note: string;
}

export interface JobEvent {
  level: string;
  stage: string | null;
  message: string;
  data: Record<string, unknown>;
  created_at: string | null;
}

export interface Job {
  id: string;
  kind: string;
  status: string;
  stage: string | null;
  progress: number;
  attempts: number;
  max_attempts: number;
  priority: number;
  dedupe_key: string | null;
  error: string | null;
  error_type: string | null;
  result: Record<string, unknown>;
  run_after: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  worker_id: string | null;
  events?: JobEvent[] | null;
}

export interface JobListResponse {
  items: Job[];
  pagination: Pagination;
}

export interface OkResponse {
  ok: boolean;
  detail: string | null;
}

export interface TestCandidate {
  id: string;
  repository_id: string;
  review_id: string | null;
  finding_id: string | null;
  target_path: string;
  target_symbol: string | null;
  suggested_test_path: string;
  rationale: string | null;
  provider: string;
  model: string;
  is_mock_provider: boolean;
  status: string;
  executed_at: string | null;
  sandbox_mode: string | null;
  sandbox_detail: string | null;
  exit_code: number | null;
  duration_ms: number | null;
  output_truncated: boolean;
  timed_out: boolean;
  collected_tests: number | null;
  run_metadata: Record<string, unknown>;
  execution_disabled_reason: string | null;
  created_at: string;
  source?: string;
  stdout_tail?: string | null;
  stderr_tail?: string | null;
}

export interface TestCandidateListResponse {
  items: TestCandidate[];
  pagination: Pagination;
}

export interface TestCandidateDetail extends TestCandidate {
  source: string;
  stdout_tail: string | null;
  stderr_tail: string | null;
}

export interface TestGenerationResponse {
  candidates: TestCandidate[];
  job_id: string | null;
  executed_synchronously: boolean;
  note: string | null;
}

export interface TestExecutionResponse {
  candidate: TestCandidateDetail;
  job_id: string | null;
  executed_synchronously: boolean;
  note: string | null;
}

export interface SandboxStatus {
  requested_mode: string;
  effective_mode: string;
  available: boolean;
  reason: string | null;
  detail: string;
  limits: Record<string, unknown>;
}

export interface EvalDataset {
  id: string;
  name: string;
  version: string;
  kind: string;
  repository: string | null;
  description: string | null;
  commit_sha: string | null;
  provenance: string | null;
  license_note: string | null;
  limitations: string | null;
  source_path: string | null;
  case_count: number;
  split_counts: Record<string, number>;
  content_hash: string;
  created_at: string | null;
  status: string;
  latest_run: EvalRun | null;
}

export interface EvalRun {
  id: string;
  dataset_id: string;
  dataset_name: string | null;
  dataset_kind: string | null;
  kind: string;
  status: string;
  sample_count: number;
  metrics: Record<string, unknown>;
  strategy_metrics: Record<string, Record<string, unknown>>;
  config: Record<string, unknown>;
  limitations: string[];
  notes: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
}

export interface EvalCaseResult {
  id: string;
  case_id: string;
  case_key: string;
  split: string;
  strategy: string;
  predictions: Array<Record<string, unknown>>;
  metrics: Record<string, unknown>;
  latency_ms: number | null;
  error: string | null;
}

export interface EvalRunDetail extends EvalRun {
  results: EvalCaseResult[];
}

export interface EvalDatasetListResponse {
  items: EvalDataset[];
}

export interface EvalCase {
  id: string;
  case_key: string;
  split: string;
  query: string;
  labels: Array<Record<string, unknown>>;
  expected_count: number;
  notes: string | null;
}

export interface EvalDatasetDetail extends EvalDataset {
  cases: EvalCase[];
}

export interface Overview {
  repositories: {
    total: number;
    indexed: number;
    by_status: Record<string, number>;
  };
  reviews: {
    total: number;
    by_status: Record<string, number>;
    terminal: number;
    success_rate: number | null;
    latency_ms: {
      p50: number | null;
      p95: number | null;
      sample_size: number;
      scope: string;
    };
  };
  findings: {
    total: number;
    non_rejected: number;
    open_findings: number;
    labelled_findings: number;
    by_severity: Record<string, number>;
    by_verification_status: Record<string, number>;
    by_category: Record<string, number>;
    open_definition: string;
  };
  jobs: {
    by_status: Record<string, number>;
    active: number;
    terminal: number;
    failure_rate: number | null;
  };
  test_intelligence: {
    candidates: number;
    by_status: Record<string, number>;
    executed: number;
    execution_disabled: number;
  };
  evaluation: {
    datasets: number;
    runs: number;
    latest_run_id: string | null;
    latest_run_status: string | null;
    latest_run_at: string | null;
  };
  severity_order: string[];
  recent_reviews: Review[];
  recent_findings: Finding[];
  recent_jobs: Job[];
}

export interface SettingsResponse {
  app_name: string;
  version: string;
  environment: string;
  demo_mode: boolean;
  llm: ProviderSummary;
  embedding: ProviderSummary;
  retrieval: RetrievalSummary;
  security: {
    auth_required: boolean;
    cors_origins: string[];
    trust_proxy_headers: boolean;
    max_request_bytes: number;
    webhook_signature_verification: boolean;
  };
  limits: {
    index_max_files: number;
    index_max_file_bytes: number;
    review_max_findings: number;
    job_timeout_seconds: number;
    llm_timeout_seconds: number;
  };
  test_generation: {
    enabled: boolean;
    max_candidates: number;
  };
  test_execution: {
    mode: string;
    network_enabled: boolean;
    timeout_seconds: number;
    memory_mb: number;
    detail: string;
  };
  github: {
    app_configured: boolean;
    webhook_secret_configured: boolean;
    publish_enabled: boolean;
    comment_severities: string[];
    max_comments_per_review: number;
    note: string;
  };
}

export interface GitHubInstallation {
  id: string;
  installation_id: string;
  account_login: string;
  account_type: string;
  repository_selection: string;
  repository_count: number;
  suspended: boolean;
  permissions: Record<string, unknown>;
  events: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface WebhookDelivery {
  id: string;
  delivery_id: string;
  event: string;
  action: string | null;
  repository_full_name: string | null;
  sender: string | null;
  signature_verified: boolean;
  status: string;
  payload_bytes: number;
  payload_sha256: string;
  job_id: string | null;
  error: string | null;
  received_at: string | null;
  processed_at: string | null;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
