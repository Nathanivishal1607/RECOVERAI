// Mirrors backend/schemas/dashboard.py and backend/schemas/audit.py.
// Read-only projections of the Phase 1A/1B contract — see docs/decisions.

export type Action = "RETRY" | "MESSAGE" | "NO_ACTION";
export type PolicyResult = "ALLOWED" | "BLOCKED";
export type ExecutionStatus = "REQUESTED" | "ACCEPTED" | "REJECTED" | "FAILED";
export type OutcomeResult = "RECOVERED" | "NOT_RECOVERED";
export type CaseStatus =
  | "OPEN"
  | "ANALYZING"
  | "ACTION_SELECTED"
  | "ACTION_EXECUTED"
  | "WAITING_FOR_OUTCOME"
  | "RECOVERED"
  | "STOPPED"
  | "EXPIRED"
  | "FAILED";

export interface ActionConsideration {
  action: Action;
  recovery_probability: number | null;
  incremental_probability: number | null;
  eirv_value: number | null;
  cost_used: number | null;
  policy_result: PolicyResult | null;
  policy_reason_code: string | null;
  is_recommended: boolean;
  is_final: boolean;
}

export interface ModelVersionRef {
  id: string;
  model_role: string;
  model_name: string;
  version: string;
  algorithm: string | null;
  status: string;
  feature_schema_id: string | null;
  training_dataset_snapshot_id: string | null;
}

export interface CycleSummary {
  cycle_number: number;
  decision_timestamp: string;
  recommended_action: Action;
  final_action: Action;
  was_blocked: boolean;
  intervention_action: Action | null;
  execution_status: ExecutionStatus | null;
  outcome_result: OutcomeResult | null;
  recovery_amount: string | null;
}

export interface DecisionAuditRead {
  decision_record_id: string;
  recovery_case_id: string;
  cycle_number: number;
  decision_timestamp: string;
  payment_amount_at_decision: string;
  status: string;

  actions_considered: ActionConsideration[];
  recommended_action: Action;
  final_action: Action;
  was_blocked: boolean;
  block_reason_codes: string[];
  decision_reason: string | null;

  policy_id: string | null;
  policy_version: string | null;
  decision_engine_version: string | null;

  intervention_action: Action | null;
  intervention_channel: string | null;
  execution_status: ExecutionStatus | null;
  intervention_cost: string | null;

  outcome_result: OutcomeResult | null;
  outcome_recovery_amount: string | null;
  outcome_observed_at: string | null;

  model_version: ModelVersionRef | null;
  previous_cycles: CycleSummary[];
}

export interface PaymentRead {
  id: string;
  display_id: string;
  merchant_id: string;
  customer_id: string;
  external_payment_id: string | null;
  amount: string;
  currency: string;
  status: string;
  payment_method: string | null;
}

export interface PaymentEventRead {
  id: string;
  payment_id: string;
  event_type: string;
  event_timestamp: string;
  created_at: string;
  attempt_number: number | null;
  amount: string | null;
  provider_event_id: string | null;
}

export interface ExperimentAssignmentRead {
  experiment_id: string;
  experiment_name: string | null;
  arm: "CONTROL" | "TREATMENT";
  assigned_at: string;
}

export interface RecoveryCaseDetailRead {
  recovery_case_id: string;
  case_display_id: string;
  payment_id: string;
  merchant_id: string;
  status: CaseStatus;
  amount_at_risk: string;
  failure_category: string | null;
  opened_at: string;
  closed_at: string | null;
  cycles: DecisionAuditRead[];
  payment: PaymentRead | null;
  payment_events: PaymentEventRead[];
  experiment_assignment: ExperimentAssignmentRead | null;
}

export interface ActionCounts {
  RETRY: number;
  MESSAGE: number;
  NO_ACTION: number;
}

export interface ExecutionStatusCounts {
  REQUESTED: number;
  ACCEPTED: number;
  REJECTED: number;
  FAILED: number;
}

export interface ActionOutcomeCounts {
  recovered: number;
  not_recovered: number;
}

export interface RecoveryByAction {
  RETRY: ActionOutcomeCounts;
  MESSAGE: ActionOutcomeCounts;
  NO_ACTION: ActionOutcomeCounts;
}

export interface HighlightedCases {
  hero_recovered_case_id: string | null;
  policy_block_case_id: string | null;
  multi_cycle_case_id: string | null;
}

export interface DashboardRead {
  total_cases: number;
  open_cases: number;
  recovered_cases: number;
  not_recovered_cases: number;
  total_amount_at_risk: string;
  total_recovery_amount: string;
  decision_cycle_count: number;
  action_counts: ActionCounts;
  no_action_count: number;
  policy_blocked_count: number;
  execution_status_summary: ExecutionStatusCounts;
  recovery_by_action: RecoveryByAction;
  highlighted_cases: HighlightedCases;
}

export interface RecoveryCaseListItem {
  recovery_case_id: string;
  case_display_id: string;
  payment_id: string;
  payment_display_id: string | null;
  payment_amount: string;
  currency: string;
  status: CaseStatus;
  cycle_count: number;
  latest_recommended_action: Action | null;
  latest_final_action: Action | null;
  latest_outcome_result: OutcomeResult | null;
  opened_at: string;
}

export interface RecoveryCaseListResponse {
  items: RecoveryCaseListItem[];
  total: number;
  limit: number;
  offset: number;
}
