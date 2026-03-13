export interface TraceSummary {
  trace_id: string;
  session_id: string;
  tags: string[];
  query_preview?: string | null;
  turn_index?: number | null;
}

export interface EvalDatasetExportResponse {
  trace_count: number;
  dataset_path: string;
  manifest_path: string;
}

export interface EvalDatasetLoadResponse {
  dataset_path: string;
  manifest_path?: string | null;
  counts: {
    total: number;
    by_split: Record<string, number>;
  };
  examples: Array<{
    example_id: string;
    split: string;
    question: string;
  }>;
}

export interface EvalDatasetBrowseEntry {
  path: string;
  label: string;
  is_default: boolean;
}

export interface EvalMetricBrowseEntry {
  metric_spec: string;
  label: string;
  source_spec_path: string;
}

export interface EvalRunResponse {
  run_id: string;
  counts: {
    total: number;
    val: number;
    test: number;
  };
  min_test_score?: number | null;
  passed_threshold: boolean;
  cases: Array<{
    example_id: string;
    split: string;
    score: number;
    feedback?: string | null;
    pred_trace_id: string;
    pred_session_id: string;
    question: string;
  }>;
}
