#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Run the JFC Phase 1 model-comparison study.

Usage:
  scripts/run_jfc_phase1_study.sh [options]

Options:
  --dry-run            Print the commands without running them.
  --rerun-existing     Archive existing model output folders before running.
  --only SLUG          Run one model slug only, e.g. gpt-5.4-mini.
  --output-root DIR    Output parent directory.
                       Default: analyses/results/higgs2tautau
  --task-file FILE     Prompt/task markdown file.
                       Default: tasks/jfc-cms-h2tautau.md
  --max-turns N        JFC max turns. Default: 50.
  --max-iterations N   JFC max review iterations per phase. Default: 5.
  -h, --help           Show this help.

The script writes:
  analyses/results/higgs2tautau/_study_logs/*.log
  analyses/results/higgs2tautau/jfc_phase1_study_summary.csv
  analyses/results/higgs2tautau/jfc_phase1_study_summary.md
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

task_file="tasks/jfc-cms-h2tautau.md"
output_root="analyses/results/higgs2tautau"
analysis_type="measurement"
max_turns="50"
max_iterations="5"
dry_run=0
rerun_existing=0
only_slug=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --rerun-existing)
      rerun_existing=1
      shift
      ;;
    --only)
      only_slug="${2:?--only requires a model slug}"
      shift 2
      ;;
    --output-root)
      output_root="${2:?--output-root requires a directory}"
      shift 2
      ;;
    --task-file)
      task_file="${2:?--task-file requires a file}"
      shift 2
      ;;
    --max-turns)
      max_turns="${2:?--max-turns requires a value}"
      shift 2
      ;;
    --max-iterations)
      max_iterations="${2:?--max-iterations requires a value}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$task_file" ]]; then
  echo "Task file not found: $task_file" >&2
  exit 1
fi

mkdir -p "$output_root"

log_dir="$output_root/_study_logs"
archive_dir="$output_root/_archive"
summary_csv="$output_root/jfc_phase1_study_summary.csv"
summary_md="$output_root/jfc_phase1_study_summary.md"
mkdir -p "$log_dir"

# Fields:
#   model_spec|slug|input_cost_per_1m|output_cost_per_1m|context_window|max_output_tokens
models=(
  'ollama:gemma-4-31B-it|gemma-4-31B-it|0|0|262144|remaining_context'
  'amsc:claude-sonnet-4-6|claude-sonnet-4-6|3|15|1000000|64000'
  'amsc:gpt-5.4-mini|gpt-5.4-mini|0.75|4.5|272000|128000'
  'cborg:gemini-3-flash-high|gemini-3-flash-high|0.25|3|1048576|65535'
  'amsc:gpt-5.4-high|gpt-5.4-high|2.5|15|1050000|128000'
)

if [[ -n "$only_slug" ]]; then
  found=0
  for model_row in "${models[@]}"; do
    IFS='|' read -r _ slug _ _ _ _ <<< "$model_row"
    if [[ "$slug" == "$only_slug" ]]; then
      found=1
      break
    fi
  done
  if [[ "$found" -eq 0 ]]; then
    echo "No configured model matches --only '$only_slug'" >&2
    exit 2
  fi
fi

write_summary_headers() {
  printf 'model_spec,slug,input_cost_per_1m,output_cost_per_1m,context_window,max_output_tokens,exit_code,elapsed_seconds,strategy_path,strategy_bytes,strategy_sha256,log_path\n' > "$summary_csv"
  {
    printf '# JFC Phase 1 Study Summary\n\n'
    printf '| Model | Slug | Input $/1M | Output $/1M | Exit | Elapsed (s) | STRATEGY.md | Log |\n'
    printf '| --- | --- | ---: | ---: | ---: | ---: | --- | --- |\n'
  } > "$summary_md"
}

csv_escape() {
  local value="$1"
  value="${value//\"/\"\"}"
  printf '"%s"' "$value"
}

append_summary() {
  local model_spec="$1"
  local slug="$2"
  local input_cost="$3"
  local output_cost="$4"
  local context_window="$5"
  local max_output_tokens="$6"
  local exit_code="$7"
  local elapsed="$8"
  local strategy_path="$9"
  local strategy_bytes="${10}"
  local strategy_sha="${11}"
  local log_path="${12}"

  {
    csv_escape "$model_spec"; printf ','
    csv_escape "$slug"; printf ','
    csv_escape "$input_cost"; printf ','
    csv_escape "$output_cost"; printf ','
    csv_escape "$context_window"; printf ','
    csv_escape "$max_output_tokens"; printf ','
    csv_escape "$exit_code"; printf ','
    csv_escape "$elapsed"; printf ','
    csv_escape "$strategy_path"; printf ','
    csv_escape "$strategy_bytes"; printf ','
    csv_escape "$strategy_sha"; printf ','
    csv_escape "$log_path"; printf '\n'
  } >> "$summary_csv"

  local strategy_cell="missing"
  if [[ -n "$strategy_path" ]]; then
    strategy_cell="\`$strategy_path\` (${strategy_bytes} bytes)"
  fi

  printf '| `%s` | `%s` | %s | %s | %s | %s | %s | `%s` |\n' \
    "$model_spec" "$slug" "$input_cost" "$output_cost" "$exit_code" "$elapsed" "$strategy_cell" "$log_path" \
    >> "$summary_md"
}

run_one_model() {
  local model_spec="$1"
  local slug="$2"
  local input_cost="$3"
  local output_cost="$4"
  local context_window="$5"
  local max_output_tokens="$6"

  if [[ -n "$only_slug" && "$slug" != "$only_slug" ]]; then
    return 0
  fi

  local output_dir="$output_root/$slug"
  local run_id
  run_id="$(date -u +%Y%m%dT%H%M%SZ)"
  local log_path="$log_dir/${slug}_${run_id}.log"
  local strategy_path="$output_dir/phase1_strategy/outputs/STRATEGY.md"

  if [[ -e "$output_dir" ]]; then
    if [[ "$rerun_existing" -eq 1 ]]; then
      local archived_path="$archive_dir/${slug}_${run_id}"
      if [[ "$dry_run" -eq 1 ]]; then
        echo "Would archive existing output: $output_dir -> $archived_path"
      else
        mkdir -p "$archive_dir"
        echo "Archiving existing output: $output_dir -> $archived_path"
        mv "$output_dir" "$archived_path"
      fi
    else
      echo "Skipping existing output folder: $output_dir"
      local strategy_bytes=""
      local strategy_sha=""
      local existing_strategy=""
      if [[ -f "$strategy_path" ]]; then
        existing_strategy="$strategy_path"
        strategy_bytes="$(wc -c < "$strategy_path" | tr -d ' ')"
        strategy_sha="$(sha256sum "$strategy_path" | awk '{print $1}')"
      fi
      append_summary "$model_spec" "$slug" "$input_cost" "$output_cost" "$context_window" "$max_output_tokens" "skipped" "0" "$existing_strategy" "$strategy_bytes" "$strategy_sha" ""
      return 0
    fi
  fi

  local cmd=(
    uv run hepagent jfc run
    -n "$slug"
    -t "$analysis_type"
    -p "$task_file"
    --base-dir "$output_root"
    --model "$model_spec"
    --max-turns "$max_turns"
    --max-iterations "$max_iterations"
    --yolo
  )

  echo "Running $model_spec -> $output_dir"
  printf 'Command:'
  printf ' %q' "${cmd[@]}"
  printf '\n'

  if [[ "$dry_run" -eq 1 ]]; then
    append_summary "$model_spec" "$slug" "$input_cost" "$output_cost" "$context_window" "$max_output_tokens" "dry-run" "0" "" "" "" "$log_path"
    return 0
  fi

  local start_time end_time elapsed exit_code
  start_time="$(date +%s)"
  set +e
  "${cmd[@]}" > "$log_path" 2>&1
  exit_code=$?
  set -e
  end_time="$(date +%s)"
  elapsed=$((end_time - start_time))

  local produced_strategy=""
  local strategy_bytes=""
  local strategy_sha=""
  if [[ -f "$strategy_path" ]]; then
    produced_strategy="$strategy_path"
    strategy_bytes="$(wc -c < "$strategy_path" | tr -d ' ')"
    strategy_sha="$(sha256sum "$strategy_path" | awk '{print $1}')"
  fi

  append_summary "$model_spec" "$slug" "$input_cost" "$output_cost" "$context_window" "$max_output_tokens" "$exit_code" "$elapsed" "$produced_strategy" "$strategy_bytes" "$strategy_sha" "$log_path"

  if [[ "$exit_code" -ne 0 ]]; then
    echo "Model run exited non-zero ($exit_code): $model_spec"
    echo "Log: $log_path"
  fi
}

write_summary_headers

for model_row in "${models[@]}"; do
  IFS='|' read -r model_spec slug input_cost output_cost context_window max_output_tokens <<< "$model_row"
  run_one_model "$model_spec" "$slug" "$input_cost" "$output_cost" "$context_window" "$max_output_tokens"
done

echo "Study summary:"
echo "  $summary_csv"
echo "  $summary_md"
