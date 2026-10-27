# V1 acceptance evidence map

This map keeps the original 30 criteria distinct from evidence status. Existing
test references show where behavior is exercised; they do not imply that tests
were run in this environment or that live staging acceptance occurred.

| # | V1 criterion | Specific evidence | Status |
|---:|---|---|---|
| 1 | Explicit developer activation | `tests/test_harness_command.py::test_command_launches_the_dashboard_in_the_background_on_loopback` | passed in the 757-test non-browser regression |
| 2 | Repository investigation before design questions | `tests/test_analyzer.py::test_report_keeps_facts_assumptions_and_questions_separate` | passed in the 757-test non-browser regression |
| 3 | Interview depth adapts to task complexity | `tests/test_classification.py::test_interview_depth_is_strictly_nested_and_complex_covers_all` | passed in the 757-test non-browser regression |
| 4 | Clarify important ambiguity rather than inventing requirements | `tests/test_analyzer.py::test_malformed_decision_becomes_a_question` | passed in the 757-test non-browser regression |
| 5 | Recommendations preserve developer authority | `tests/test_decision_cli.py::test_accepting_a_decision_recommendation_records_it` | passed in the 757-test non-browser regression |
| 6 | Produce a complete Markdown specification | `tests/test_spec_record.py::test_round_trip_of_every_field` | passed in the 757-test non-browser regression |
| 7 | Generate relevant architecture diagrams | `tests/test_dashboard_diagrams.py::test_the_sample_design_renders_every_relevant_type_with_addressable_elements` | automated and native Chrome visual checks passed; cosmetic finding below |
| 8 | Accept and resolve diagram comments | `tests/test_phase2_acceptance.py::test_the_wrong_connection_scenario_end_to_end` | automated and native Chrome visual checks passed; cosmetic finding below |
| 9 | Produce an approved test plan | `tests/test_testplan.py::test_provenance_and_only_effective_scenarios_are_plan_content` | passed in the 757-test non-browser regression |
| 10 | Perform a mandatory completeness review | `tests/test_review.py::test_bare_task_reports_all_ten_items_in_spec_order` | passed in the 757-test non-browser regression |
| 11 | Prevent implementation before explicit approval | `tests/test_approval.py::test_nothing_can_be_handed_off_without_an_approval_record` | passed in the 757-test non-browser regression |
| 12 | Persist tasks and resume interrupted sessions | `tests/test_resume_cli.py::test_killed_mid_interview_resume_continues_from_the_same_question` | passed in the 757-test non-browser regression |
| 13 | Store approved decisions in Git-versioned Markdown | `tests/test_registry.py::test_record_round_trips_every_field` | passed in the 757-test non-browser regression |
| 14 | Maintain local runtime state in SQLite | `tests/test_controller.py::test_state_survives_reconnect` | passed in the 757-test non-browser regression |
| 15 | Preserve decision rationale and history | `tests/test_decision_cli.py::test_latest_by_default_history_on_request` | passed in the 757-test non-browser regression |
| 16 | Execute verified read-only investigations safely | `tests/test_analyzer.py::test_analyze_leaves_repo_and_git_dir_untouched` | passed in the 757-test non-browser regression |
| 17 | Require approval for potentially mutating operations | `tests/test_phase4_core_safety.py::test_unknown_effects_are_never_automatic` | passed in the 757-test non-browser regression |
| 18 | Directly invoke the existing AL Orchestrator | `tests/test_execution_operations.py::test_actual_native_controller_reads_guarded_al_and_resume` | covered; native Codex integration unavailable in this environment |
| 19 | Receive structured AL agent status and progress | `tests/test_phase3_acceptance.py::test_real_approval_detached_worker_decision_and_reconnect` | covered; native Codex integration unavailable in this environment |
| 20 | Display agents, progress and blockers in dashboard | `tests/test_dashboard_execution_browser.py::test_execution_panel_keeps_decision_draft_and_fits_desktop_and_mobile` | real native Chrome Headless Shell desktop/mobile check passed (2026-10-27) |
| 21 | Display live execution activity | `tests/test_dashboard_execution.py::test_execution_get_does_not_launch_and_logs_are_scoped` | passed in the 757-test non-browser regression |
| 22 | Track merge requests and CI/CD | `tests/test_gitlab.py::test_protocol_encoding_pagination_and_secret_redaction` | passed in the 757-test non-browser regression |
| 23 | Detect staging deployment readiness | `tests/test_delivery_monitor.py::test_publish_reconciles_and_success_requires_current_staging_deployment` | passed in the 757-test non-browser regression |
| 24 | Require approval before browser staging validation | `tests/test_browser_validation.py::test_separate_approval_and_no_repeat` | approval-gate tests passed; external staging unverified |
| 25 | Report browser acceptance results | `tests/test_browser_validation.py::test_failure_is_investigation_without_authority` and `tests/test_browser_recovery.py::test_receipt_before_database_insert_recovers_without_driver` | reporting/recovery tests passed; local Chrome passed; external staging unverified |
| 26 | Present a concise final execution summary | `tests/test_reporting.py::test_missing_delivery_and_test_counts_are_unavailable` | reporting tests passed; release closeout recorded separately |
| 27 | Keep local execution state private | `tests/test_fixture_profile.py::test_real_fixture_setup_is_isolated_and_database_teardown_closes_connection` | passed in the 757-test non-browser regression |
| 28 | Share approved specs and decisions through Git | `tests/test_decision_cli.py::test_fresh_clone_reads_every_approved_decision_and_the_chain` | passed in the 757-test non-browser regression |
| 29 | Prevent silent architecture decisions | `tests/test_review.py::test_each_signal_blocks_its_item_and_routes_back` | passed in the 757-test non-browser regression |
| 30 | Preserve traceability to implementation runs | `tests/test_execution.py::test_exact_private_handoff_and_idempotent_reservation` | passed in the 757-test non-browser regression |

Real browser capability and the selected dashboard/petshop desktop/mobile checks
passed on 2026-10-27: all 18 browser-marked tests, plus the earlier selected
11-check browser/driver run. Final screenshots were visually inspected. The
developer-approved static petshop was served on loopback. This does not establish
an external staging deployment, real GitLab/GitHub provider pipeline, or signed-in
model-provider acceptance. Fixture transport and injected-driver results retain
their fixture provenance. The non-browser regression passed 757 tests, with
17 native/environment skips. Publication and installation receipts are separate.

Visual finding: existing component-diagram arrows cross some edge labels. This
is a cosmetic readability issue; browser interaction and viewport checks pass.
See the original screenshots and visual review at
`/private/tmp/phase5-complete-native-browser/` and
`/private/tmp/phase5-visual-review.md`.

The real staging run and external provider release verification remain outstanding;
the developer requested the local petshop example for this session.
Fixture-provider HTTP tests exercise transport and failure behavior and do not
establish live provider acceptance.
