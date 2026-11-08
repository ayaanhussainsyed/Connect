# Graph Report - Connect  (2026-11-07)

## Corpus Check
- 141 files · ~125,883 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 11 file(s) not represented in the graph (top: .parquet 3, .joblib 2, .ipynb 2)

## Summary
- 2257 nodes · 6278 edges · 101 communities (94 shown, 7 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 431 edges (avg confidence: 0.89)
- Token cost: 485,113 input · 0 output

## Community Hubs (Navigation)
- Ask Connect Graph RAG
- LLM Clients (Claude, Astra)
- Confirmation and Where It Shows
- Engine Data Loaders
- Message Bridge and Event Log
- Flask App and Audit Core
- Closed Today Emergency Flow
- Season Autopilot
- Weekly Drift Model
- Owner Card Route Tests
- MongoDB Access Guard
- Engine Prediction API
- Inbound Reply Webhooks
- Message Slot Checks
- Engine Assembly and Calibration
- Decision Flow and Follow-ups
- Feature Builder
- Habit-Type Model (M1)
- Predictions CSV Export
- Season Card Composer
- Yext Entity Reads
- Guardrail Tests
- Review Reply Drafts
- Profile Drift Suggestions
- Demo Page Routes
- Dataset Loaders
- Shown-Hours Explanations
- Yext Writer and Confirmation
- Hours Ranker (M3)
- Signed Link and Hours Tests
- Closed-for-Good Scorer
- Hours Validation Utilities
- Channel Base Interface
- Owner Card Pages
- Signed Single-Use Links
- Suggestion Engine Adapter
- Guardrail Rules
- Live Holiday Calendar
- Emergency and Question Messages
- Time-Cutoff Data Access
- Holiday Map Diffing
- Yext Sandbox CLI
- A1 Explain Pages
- Expected-Points Decision Rule
- Outbox and Phone Masking
- Domain Records
- Outgoing Message Wrapper
- Flask App Tests
- Profile Test Fixtures
- Suggestions Interface
- ML Findings and Milestones
- Outbox Channel
- Confirmation and Autopilot Notes
- Detect and Suggest Requirements
- Score Engine Findings
- Template Globals and Partials
- WhatsApp Channel
- Audit Chain Verification
- Channel Router
- README Draft and Leakage Controls
- Apply, Emergency, Video Plan
- WhatsApp Template Params
- Holiday History Index
- Fonecta Baseline Engine
- Holiday Hours Merge
- Engine Contract and Models
- Challenge and Five-Step Chain
- Terminal Demo Script
- Learning and Autopilot Plan
- Whole-Profile Features
- Prediction Rows and Fallback
- Settings Config
- Regular-Hours Versions
- Profile CLI
- Owner Screens O1-O3
- Ablation Results
- Model Stack M2-M4
- Messaging and Reply Parsing Plan
- Suggestions API Route
- Users and Ops Console Design
- README Chain and Yext Behaviour
- Entity Snapshots
- Agent Working Agreement
- LLM Roles and Safety
- Season Labels
- Setup Log and Yext Mock
- Guardrails, Owner-Wins, Radar Plan
- Builder-ML Contract
- No-History and Deliverables
- Mock Yext Server
- Build Order and Scorer
- Card and Permission States
- Card Message Fallback
- Message Design Principles
- Live Calendar Sources
- Secrets Handling
- Message Clarity Judge
- Hours Utilities Doc
- AI Act Disclosure

## God Nodes (most connected - your core abstractions)
1. `Suggestion` - 49 edges
2. `YextClient` - 46 edges
3. `get_db()` - 42 edges
4. `log()` - 41 edges
5. `handle_reply()` - 38 edges
6. `handle_decision()` - 35 edges
7. `handle_emergency()` - 35 edges
8. `explain_shown()` - 34 edges
9. `run_autopilot()` - 33 edges
10. `live()` - 31 edges

## Surprising Connections (you probably didn't know these)
- `card_error.html (link error page)` --conceptually_related_to--> `TokenError`  [INFERRED]
  app/templates/card_error.html → cs/links.py
- `Flip confirmation tick` --conceptually_related_to--> `is_flip()`  [INFERRED]
  app/templates/card.html → cs/season.py
- `WhatsApp 24-hour session window` --conceptually_related_to--> `whatsapp_inbound()`  [INFERRED]
  docs/WHATSAPP_TEMPLATE.md → app/webhooks.py
- `_ops_verify.html (hash-chain verification result)` --shares_data_with--> `verify_chain()`  [INFERRED]
  app/templates/_ops_verify.html → cs/audit.py
- `Season Autopilot note` --conceptually_related_to--> `run_autopilot()`  [INFERRED]
  docs/MESSAGES.md → cs/autopilot.py

## Import Cycles
- 2-file cycle: `cs/suggest/__init__.py -> cs/suggest/engine_adapter.py -> cs/suggest/__init__.py`

## Hyperedges (group relationships)
- **Detect → Suggest → Contact → Decide → Apply chain** — graphify_out_context_challenge_brief_detect, graphify_out_context_challenge_brief_suggest, graphify_out_context_challenge_brief_contact, graphify_out_context_challenge_brief_decide, graphify_out_context_challenge_brief_apply [EXTRACTED 1.00]
- **SUGGEST pipeline: habit types + state model + ranker → expected points + flip margin** — docs_plan_habit_type_model, docs_plan_histgradientboosting, docs_plan_hours_ranker, docs_plan_expected_points_decision, docs_plan_flip_safety_margin [EXTRACTED 1.00]
- **Learning loop: answers → learned facts → standing permission, measured by pilot replay** — docs_plan_a11_learning, docs_plan_b5_learning_from_answers, docs_plan_learned_fact_decay, docs_plan_approve_every_year, docs_plan_pilot_log_replay [EXTRACTED 1.00]
- **Compulsory Part A chain: detect, suggest, contact, decide, apply** — readme_five_step_chain, readme_what_google_shows_now, readme_preventing_wrong_suggestions, readme_message_a7, readme_no_login_decision, readme_apply_and_confirm [EXTRACTED 1.00]
- **Score engine model stack M1-M5 (habit types, state model, hours ranker, decision rule, closed-for-good)** — docs_ml_findings_m1_habit_types, docs_ml_findings_m2_state_model, docs_ml_findings_m3_hours_ranker, docs_ml_findings_m4_decision_rule, docs_ml_findings_m5_closed_for_good [EXTRACTED 1.00]
- **Part B innovations targeting problems the plain suggest-approve loop misses** — readme_season_autopilot, readme_per_publisher_deadlines, readme_one_smart_question, readme_closed_today, readme_owner_wins_concurrency, readme_ask_connect_graph_rag, readme_suggestion_loop [EXTRACTED 1.00]
- **Phase A-1 five-step chain (Detect -> Suggest -> Contact -> Decide -> Apply)** — docs_build_plan_a_display_shown_hours, docs_build_plan_a_baseline_engine, docs_build_plan_a_guardrails, docs_build_plan_a_compose, docs_build_plan_a_channels, docs_build_plan_a_links, docs_build_plan_a_flask_app, docs_build_plan_a_yext_client, docs_build_plan_a_confirm, docs_build_plan_a_demo_script [EXTRACTED 1.00]
- **A3 suggestion model stack (M0 fallback, M1-M4)** — docs_app_design_m0_baseline_rules, docs_app_design_m1_habit_type_model, docs_app_design_m2_state_model, docs_app_design_m3_hours_ranker, docs_app_design_m4_decision_rule [EXTRACTED 1.00]
- **Listing-change safety path (guardrails, owner-wins, audited Yext write)** — docs_build_plan_a_guardrails, docs_build_plan_a_standing_permission, docs_build_plan_a_owner_wins_check, docs_build_plan_a_yext_client, docs_build_plan_a_write_record, docs_build_plan_a_audit_log [EXTRACTED 1.00]
- **Owner season-card web flow (view, preview, decide, undo)** — app_templates_card, app_templates_card_card_form, app_templates__card_hints, app_templates_card_done, app_templates_card_error, app_owner_card_page, app_owner_card_preview, app_owner_card_decide, app_owner_card_undo [INFERRED 0.95]
- **Closed-today emergency flow (request, unknown-sender confirm, fast-publisher result, undo)** — docs_messages_closed_today, app_templates_card_closed_today_button, app_templates__emergency_result, app_templates_emergency_confirm, app_owner_card_closed_today, app_owner_em_confirm, app_owner_em_undo, cs_emergency_handle_emergency [INFERRED 0.85]
- **WhatsApp first-contact flow (template outside session, then full card in session)** — docs_whatsapp_template_connect_season_card, cs_compose_compose_card_message, cs_compose_compose_template_params, cs_compose_sanitize_template_param, cs_channels_whatsapp_whatsappchannel, docs_whatsapp_template_24_hour_session, cs_channels_outbox_record_inbound, cs_channels_router_send [EXTRACTED 1.00]

## Communities (101 total, 7 thin omitted)

### Community 0 - "Ask Connect Graph RAG"
Cohesion: 0.05
Nodes (41): ask(), check_answer(), _day_name(), fact_lines(), _fmt_date(), _fmt_hours(), keep_cited_sentences(), template_answer() (+33 more)

### Community 1 - "LLM Clients (Claude, Astra)"
Cohesion: 0.05
Nodes (52): _canon_interval(), astra_available(), astra_text(), claude_available(), claude_text(), _clause_dates(), _clause_ranges(), _conflicts() (+44 more)

### Community 2 - "Confirmation and Where It Shows"
Cohesion: 0.06
Nodes (45): _emergency_result.html (closed-today result partial), card_done.html (saved / undone / declined result page), Where it shows table (publisher ETAs), _as_utc(), dataset_id(), deadline_by_publisher(), deadline_for(), eta_text() (+37 more)

### Community 3 - "Engine Data Loaders"
Cohesion: 0.08
Nodes (36): analytics(), calendar(), contacts(), cycle(), day_str(), displayed(), entities(), entries_as_of() (+28 more)

### Community 4 - "Message Bridge and Event Log"
Cohesion: 0.07
Nodes (27): log(), _apply_plan(), digit_meaning(), record_inbound(), _remember_digits(), send(), save(), can_transition() (+19 more)

### Community 5 - "Flask App and Audit Core"
Cohesion: 0.07
Nodes (21): batch(), wrapper(), _body(), canonical_json(), compute_hash(), _default(), _flush(), verify_events() (+13 more)

### Community 6 - "Closed Today Emergency Flow"
Cohesion: 0.10
Nodes (35): batched(), contact(), answer_open_again(), _apply(), _compose(), confirm_emergency(), _dates(), _db() (+27 more)

### Community 7 - "Season Autopilot"
Cohesion: 0.10
Nodes (29): _contact_flag(), _covers(), crm_permission(), _db(), demo_reset(), newest_facts(), _record_fact_once(), run_autopilot() (+21 more)

### Community 8 - "Weekly Drift Model"
Cohesion: 0.05
Nodes (17): parse_week_statement(), published_weeks(), _shift_hours(), signals(), _t(), true_weeks(), _week_from_edits(), learned_value() (+9 more)

### Community 9 - "Owner Card Route Tests"
Cohesion: 0.15
Nodes (38): Decision, is_flip(), load_card(), client(), test_bad_links(), test_closed_today_from_the_card_page(), test_decide_then_replay_then_undo(), test_demo_and_ops_pages() (+30 more)

### Community 10 - "MongoDB Access Guard"
Cohesion: 0.10
Nodes (21): allowed_db_names(), app_db_name(), _collection_for(), DatabaseNotAllowed, drop_test_db(), ensure_indexes(), get(), get_client() (+13 more)

### Community 11 - "Engine Prediction API"
Cohesion: 0.10
Nodes (18): _basis_for(), _evidence(), explain(), _normalise(), pred_path(), predict(), question_value(), run() (+10 more)

### Community 12 - "Inbound Reply Webhooks"
Cohesion: 0.08
Nodes (19): _card_for_context(), sms_inbound(), whatsapp_inbound(), whatsapp_verify(), build_interactive(), build_template(), build_text(), is_template_reply() (+11 more)

### Community 13 - "Message Slot Checks"
Cohesion: 0.12
Nodes (26): extract_slots(), fmt_date(), value_intervals(), allowed_numbers(), check_slots(), many(), sug(), test_autopilot_note() (+18 more)

### Community 14 - "Engine Assembly and Calibration"
Cohesion: 0.11
Nodes (17): habit_model(), load_engine(), assemble(), Calibrator, decide(), expected_points(), crossfit_decide(), decide_frame() (+9 more)

### Community 15 - "Decision Flow and Follow-ups"
Cohesion: 0.13
Nodes (19): followup_message(), _apply(), card_link(), _db(), DecisionError, _digit_button(), _final_guards(), handle_decision() (+11 more)

### Community 16 - "Feature Builder"
Cohesion: 0.13
Nodes (17): base_rows(), build(), build_candidates(), Context, _cycle_index(), _event_feats(), _extend_close(), link_weights_from_truth() (+9 more)

### Community 17 - "Habit-Type Model (M1)"
Cohesion: 0.12
Nodes (5): entry_observations(), HabitModel, _logsumexp(), select_k(), add_m1()

### Community 18 - "Predictions CSV Export"
Cohesion: 0.11
Nodes (18): build(), default_csv_path(), ExportResult, labels_path(), run(), save_to_mongo(), score_csv(), score_in_process() (+10 more)

### Community 19 - "Season Card Composer"
Cohesion: 0.12
Nodes (20): _blank(), compose_card_message(), render(), compose_followup(), contact_for(), _deadline_line(), demo_message(), _engine_rows() (+12 more)

### Community 20 - "Yext Entity Reads"
Cohesion: 0.15
Nodes (20): _raw_put_holidays(), test_bad_input_fails_before_any_call(), test_clear_sends_an_empty_list(), test_expected_matches_live_is_written(), test_merge_keeps_other_dates(), test_only_past_dates_writes_nothing(), test_owner_already_set_the_same_value_is_not_a_conflict(), test_owner_wins_all_cancelled_sends_nothing() (+12 more)

### Community 21 - "Guardrail Tests"
Cohesion: 0.11
Nodes (24): _contact(), ctx(), _entity_with_entry(), sug(), test_apply_does_not_mutate_and_passes_clean_rows(), test_apply_downgrades_with_reason(), test_apply_first_failing_rule_decides(), test_apply_flag_is_never_messageable() (+16 more)

### Community 22 - "Review Reply Drafts"
Cohesion: 0.11
Nodes (17): cap(), detect_day_key(), confirmed_hours(), detect_lang(), draft_reply(), entities_with_complaints(), has_owner_reply(), is_complaint() (+9 more)

### Community 23 - "Profile Drift Suggestions"
Cohesion: 0.11
Nodes (19): apply_drift(), _canon_week(), changed_days(), compose_drift_message(), _day_span(), drift_row(), drift_suggestion(), _evidence() (+11 more)

### Community 24 - "Demo Page Routes"
Cohesion: 0.14
Nodes (20): demo_autopilot(), demo_clear(), demo_emergency(), demo_entity(), demo_phone(), demo_reply(), demo_send(), demo_steps() (+12 more)

### Community 25 - "Dataset Loaders"
Cohesion: 0.15
Nodes (20): _analytics_all(), calendar(), categories(), cities(), contacts(), dev_regular_truth(), _dev_truth_history_all(), listings() (+12 more)

### Community 26 - "Shown-Hours Explanations"
Cohesion: 0.11
Nodes (20): holiday_entries(), explain_shown(), _fmt_day(), main(), shown_hours(), _first_label_row(), test_cli(), test_closed_for_good_only_from_the_entities_snapshot() (+12 more)

### Community 27 - "Yext Writer and Confirmation"
Cohesion: 0.10
Nodes (17): format_confirmation(), _canon_or_raw(), canonical_hours(), canonical_weekday_hours(), _db_ok(), _full_hours(), _live_value(), main() (+9 more)

### Community 28 - "Hours Ranker (M3)"
Cohesion: 0.14
Nodes (4): Ranker, state_labels(), StateModel, Vocab

### Community 29 - "Signed Link and Hours Tests"
Cohesion: 0.12
Nodes (16): link_for(), sign(), test_copied_part_is_verbatim(), test_is_valid_rejects(), test_normalise(), test_normalise_rejects(), test_round_trip_yext_day(), test_to_yext_day_rejects_bad_input() (+8 more)

### Community 30 - "Closed-for-Good Scorer"
Cohesion: 0.13
Nodes (14): p_closed_for_good(), truth_observations(), ablate(), cfg_probs(), final_fit(), fit_all(), Fitted, labels_for() (+6 more)

### Community 31 - "Hours Validation Utilities"
Cohesion: 0.11
Nodes (18): boundary_diff(), from_yext_day(), hours_str_to_yext_day(), is_valid(), is_valid_hours(), ivs_to_str(), _loose_minutes(), m2t() (+10 more)

### Community 32 - "Channel Base Interface"
Cohesion: 0.12
Nodes (6): Channel, ChannelNotConfigured, DeliveryRecord, EmailChannel, is_configured(), render_body()

### Community 33 - "Owner Card Pages"
Cohesion: 0.18
Nodes (17): card_closed_today(), card_decide(), card_page(), card_preview(), card_undo(), _ctx(), _decision_from_form(), em_confirm() (+9 more)

### Community 34 - "Signed Single-Use Links"
Cohesion: 0.15
Nodes (11): _b64d(), _b64e(), consume(), is_used(), _mac(), release(), _secret(), TokenError (+3 more)

### Community 35 - "Suggestion Engine Adapter"
Cohesion: 0.16
Nodes (14): suggestion_id(), available(), _candidates(), _evidence(), load(), _load(), _num(), parquet_path() (+6 more)

### Community 36 - "Guardrail Rules"
Cohesion: 0.12
Nodes (10): apply_guardrails(), contact_allowed(), flag_not_message(), GuardContext, listing_active(), not_past(), permission_scope(), respects_owner_entry() (+2 more)

### Community 37 - "Live Holiday Calendar"
Cohesion: 0.16
Nodes (11): agreement(), _easter(), _last_weekday(), live_calendar(), _nth_weekday(), public_holidays(), report(), rule_days() (+3 more)

### Community 38 - "Emergency and Question Messages"
Cohesion: 0.15
Nodes (14): total_len(), compose_emergency(), compose_question(), _digit_line(), _finish(), fmt_eta(), fmt_hours(), fmt_time() (+6 more)

### Community 39 - "Time-Cutoff Data Access"
Cohesion: 0.15
Nodes (13): analytics(), _comment_ms(), cutoff_epoch_ms(), cutoff_str(), dev_truth_history(), holiday_entries_after(), pilot_log(), publisher_suggestions() (+5 more)

### Community 40 - "Holiday Map Diffing"
Cohesion: 0.21
Nodes (16): _diff(), holiday_map(), reopen_date_of(), week_map(), check(), check(), check(), _assert_resting() (+8 more)

### Community 41 - "Yext Sandbox CLI"
Cohesion: 0.17
Nodes (4): WriteRecord, _print_entity(), YextClient, test_sandbox_mapping_applies_only_to_the_sandbox()

### Community 42 - "A1 Explain Pages"
Cohesion: 0.16
Nodes (18): a1(), a1_result(), api_shown(), entity_options(), index(), _params(), suggest_page(), suggest_result() (+10 more)

### Community 43 - "Expected-Points Decision Rule"
Cohesion: 0.17
Nodes (7): D_norm(), points(), score_rows(), main(), run_variant(), card_points(), main()

### Community 44 - "Outbox and Phone Masking"
Cohesion: 0.22
Nodes (10): mask_address(), clear_thread(), _db(), ensure_indexes(), last_inbound_at(), recent(), record_inbound(), store_outgoing() (+2 more)

### Community 45 - "Domain Records"
Cohesion: 0.15
Nodes (9): _Doc, Evidence, Message, Suggestion, _to_jsonable(), _samples(), test_domain_round_trip(), test_repository_round_trip() (+1 more)

### Community 46 - "Outgoing Message Wrapper"
Cohesion: 0.18
Nodes (9): OutgoingMessage, card_message(), opted_out_entity(), outbox_db(), test_router_falls_back_to_outbox(), test_router_sends_replies_to_opted_out_owner(), test_router_skips_opted_out_owner(), test_session_opens_after_first_tap() (+1 more)

### Community 47 - "Flask App Tests"
Cohesion: 0.11
Nodes (3): client(), test_api_shown_errors(), test_api_suggestions_errors()

### Community 48 - "Profile Test Fixtures"
Cohesion: 0.13
Nodes (8): week_strs_to_yext(), on_day(), _demo_with_complaints(), FakeYext, test_apply_drift_on_the_mock_signature(), test_apply_drift_paths(), test_approve_review_reply_is_stored_not_posted(), test_reply_has_no_invented_facts()

### Community 49 - "Suggestions Interface"
Cohesion: 0.16
Nodes (11): get_suggestions(), _actions(), base25(), base26(), _fonecta_file(), test_2026_differs_from_fonecta_only_on_closed_for_good_listings(), test_backtest_identical_to_fonecta_baseline(), test_engine_adapter_falls_back_to_baseline() (+3 more)

### Community 50 - "ML Findings and Milestones"
Cohesion: 0.16
Nodes (10): Season Autopilot coverage evaluation (eval_autopilot.py, 50 seeds per grant rate), M6 weekly-hours drift detector (precision 1.00, recall 0.55), ML agent status log, Engine v1 milestone (M1 K=10 + M2/M3/M4; grouped CV 59.35), Engine v2 (spring/autumn periods, two flip margins, decay 0.95; 61.31), Final engine config (shift candidates + decline rule; 61.67 / 219 harmful), Stale outputs/predictions_ml_2026.csv (61.31 engine) - re-export note for builder, Season Autopilot grant-rate coverage table (8/12/30/50%) (+2 more)

### Community 51 - "Outbox Channel"
Cohesion: 0.18
Nodes (6): as_outgoing(), OutboxChannel, is_finnish(), real_sms_allowed(), SmsChannel, test_mask_and_sms_rules()

### Community 52 - "Confirmation and Autopilot Notes"
Cohesion: 0.17
Nodes (11): _brand(), _business(), _changes_list(), _changes_text(), compose_autopilot_note(), compose_confirmation(), _sentence(), block() (+3 more)

### Community 53 - "Detect and Suggest Requirements"
Cohesion: 0.13
Nodes (12): A1 'What Google shows now' engine, A2 Days to check (targets_2026.csv), B4 Reach owners in time (priority + approve-by deadlines), Coverage curve over permission grant rates, Finding: 63% of needed changes are shown-open-actually-closed, Fonecta baseline (54.5 dev, 224 harmful), Scored predictions (SUGGEST / NO_ACTION / FLAG points table), Step 1: Detect (+4 more)

### Community 54 - "Score Engine Findings"
Cohesion: 0.18
Nodes (10): B2 Score engine (learns each business's habits), Diadrome slowstruct learning layer, Finding: habits repeat year to year (92%), Finding: half of needed changes invisible to copy-last-year, Finding: holidays are linked (one latent habit type per business), Finding: owner entries accurate (99%) but biased toward special days, scikit-learn HistGradientBoosting (LightGBM replacement), Hours ranker over candidate hours (+2 more)

### Community 55 - "Template Globals and Partials"
Cohesion: 0.15
Nodes (14): _globals(), nice_date(), _a1_result.html (A1 explanation partial), _card_hints.html (one-question answer preview), _demo_entity.html (Yext entity hours view), htmx 2.0.4 (CDN), card.html (owner season card page), Season card decision form (Approve / Approve + every year / No thanks) (+6 more)

### Community 56 - "WhatsApp Channel"
Cohesion: 0.16
Nodes (10): SMS reply digit buttons (1-4), is_configured(), WhatsAppChannel, SMS digit replies (1-4), WHATSAPP_TEMPLATE.md - connect_season_card template spec, WhatsApp 24-hour session window, AI Act Art. 50 disclosure, Quick-reply button payloads (APPROVE|card_id, APPROVE_EVERY_YEAR|card_id, EDIT|card_id) (+2 more)

### Community 57 - "Audit Chain Verification"
Cohesion: 0.20
Nodes (11): _ops_verify.html (hash-chain verification result), _coll(), events(), head(), verify_chain(), test_batched_decorator_flushes_on_error(), boom(), test_log_and_verify_chain() (+3 more)

### Community 58 - "Channel Router"
Cohesion: 0.22
Nodes (10): _audit(), channel_for(), contact(), _dataset_id(), language_for(), opted_out(), _recipient(), send() (+2 more)

### Community 59 - "README Draft and Leakage Controls"
Cohesion: 0.19
Nodes (10): ML findings: the score engine, Pitch charts a-f (notebooks/02_results_for_pitch.ipynb), README draft by the ML agent, outputs/engine_predictions_2026.csv (frozen, md5 ba95c12b...), Frozen evaluate.py JSON result (score 61.67, 4,195 suggestions, 750 businesses contacted), README: Connect Suggests, Customer Approves (Team Connect), evaluate.py (Fonecta official scorer), predictions_2026.csv (submitted predictions) (+2 more)

### Community 60 - "Apply, Emergency, Video Plan"
Cohesion: 0.16
Nodes (15): A9 Apply and confirm (Yext write + per-publisher confirmation), B8 Emergency changes: 'closed today', Demo video script (2.5 min), FI-D0012 (next-season auto-apply case), Finding: publisher update speeds (Google 2 min … AI assistants 7 days), Fonecta fit: sidecar service, Yext as system of record, GDPR compliance (minimisation, no special-category data), Kahvila Aurora (FI-D0001) demo business (+7 more)

### Community 61 - "WhatsApp Template Params"
Cohesion: 0.24
Nodes (9): asks(), _basis(), _card_lines(), compose_template_params(), _get(), _is_closed(), reason_sentence(), sanitize_template_param() (+1 more)

### Community 62 - "Holiday History Index"
Cohesion: 0.15
Nodes (10): holiday_history(), _holiday_index(), _holiday_rows(), label_index(), labels(), test_holiday_history_respects_as_of(), test_targets_and_labels(), test_weather_forecast_only_when_published() (+2 more)

### Community 63 - "Fonecta Baseline Engine"
Cohesion: 0.25
Nodes (8): _bench(), closed_signals(), cycle(), _dates_by_key(), _hours_text(), suggest_row(), suggestions(), _tiers()

### Community 64 - "Holiday Hours Merge"
Cohesion: 0.21
Nodes (6): _future_items(), _iso(), merge_holiday_hours(), test_merge_helper_is_pure(), test_remove_nothing_to_remove_sends_nothing(), test_reopen_date_in_the_past_is_refused()

### Community 65 - "Engine Contract and Models"
Cohesion: 0.19
Nodes (11): HabitModel (posterior, day_probs), question_value() for B1's one question, Card builder (B1), M1 Habit-type model (EM mixture), M2 State model (gradient-boosted trees), M3 Hours ranker + calibration, M4 Decision rule (expected points, flip margin lambda), Planner (B4) (+3 more)

### Community 66 - "Challenge and Five-Step Chain"
Cohesion: 0.18
Nodes (13): Connect Suggests – Customer Approves: final build plan, A8 No-login decision (approve / edit / decline), Connect Suggests architecture (DETECT/SUGGEST/CONTACT/DECIDE/APPLY/LEARN/AUDIT), Build timeline (Phases 0–5, code freeze Sun 05:00), No-login owner page (live season card, signed single-use link), Web app stack (Flask + Jinja + HTMX + Tailwind + SQLite), Fonecta × Since AI 2026 Challenge Brief, Connect Suggests – Customer Approves (challenge) (+5 more)

### Community 67 - "Terminal Demo Script"
Cohesion: 0.23
Nodes (8): main(), client_for(), _mock_up(), _say(), simulate(), client(), _free_port(), mock_url()

### Community 68 - "Learning and Autopilot Plan"
Cohesion: 0.22
Nodes (6): A11 Learning (better after edit/decline, standing permission), B5 Learns from every answer, Finding: owners accept own-history suggestions 95% vs peer-based 81%, Judge review log (66 → 78 → 84/100), Evaluation criteria (suggestion quality, CX, reliability, extensibility, learning), Standing permission for recurring changes

### Community 69 - "Whole-Profile Features"
Cohesion: 0.15
Nodes (12): A13 The whole profile through the same loop, A3 Suggestion with hours and reasons, A5 Flag businesses closed for good, A6 Stay quiet when it's already right, Regular-hours drift detector, FI-D0011 (closed-for-good flag case), FI-D0013 (stay-quiet case), Holiday post from approved hours (+4 more)

### Community 70 - "Prediction Rows and Fallback"
Cohesion: 0.18
Nodes (6): _baseline_rows(), dataset_path(), iter_csv(), targets(), shown_table(), test_shown_table_covers_every_target_row()

### Community 71 - "Settings Config"
Cohesion: 0.21
Nodes (3): _env(), load_settings(), Settings

### Community 72 - "Regular-Hours Versions"
Cohesion: 0.23
Nodes (9): regular_history(), _regular_index(), regular_versions(), _version_key(), RegularResolution, resolve_regular(), week_from_yext(), test_regular_versions_in_effect() (+1 more)

### Community 73 - "Profile CLI"
Cohesion: 0.23
Nodes (6): cmd_drift(), cmd_reviews(), main(), _print_msg(), compose_review_reply_offer(), review_id_of()

### Community 74 - "Owner Screens O1-O3"
Cohesion: 0.21
Nodes (11): O3 confirmation with per-app update times, Demo view /demo, Judges / video viewers, Kahvila Aurora (FI-D0001), O1 WhatsApp season message (Finnish), cs/channels/ delivery channels, cs/compose.py message composer (A7), cs/confirm.py confirmation (+3 more)

### Community 75 - "Ablation Results"
Cohesion: 0.23
Nodes (10): Ablation study (dev 2026, whole CV re-run per signal group), Linked holidays (P(closed NYD | closed Xmas) 0.89 vs 0.36), M1 habit types (latent-class EM mixture, K=10), No-history evaluation (A12): 34.06 vs 33.24 hidden history; 36.8 vs 14.2 on 16 truly new businesses, Business's own history (score source; 61.67 -> 33.43 without it), Two-seasons-back signal (-5.9 points without it), Data sources that mattered most (measured by ablation), FI-D0017 Kahvila Kipina (no-history cafe in Turku) (+2 more)

### Community 76 - "Model Stack M2-M4"
Cohesion: 0.18
Nodes (6): M2 state model (HistGradientBoostingClassifier: closed/regular/special), M3 hours ranker (gradient-boosted candidate scorer + isotonic calibration), Fonecta baseline (54.49 dev, 53.23 back-test), scikit-learn HistGradientBoostingClassifier (gradient-boosted trees), Isotonic regression calibration, A1 What Google shows now (cs/display.py, 100% label match)

### Community 77 - "Messaging and Reply Parsing Plan"
Cohesion: 0.21
Nodes (10): A7 Owner message (fi/sv/nb/en, preferred channel), B3 Proof from reviews and Google user edits, B6 Reply in your own words (free-text parser + read-back), Finding: 53% of owners never reply (silence is the bottleneck), GPT-6 Astra (independent message-quality judge), Opus 5.5 (reads and writes language), Risks and mitigations, Traficom regulation 28 L/2025 (unverified SMS sender blocking) (+2 more)

### Community 78 - "Suggestions API Route"
Cohesion: 0.22
Nodes (9): api_suggestions(), BadRequest, _suggest_ctx(), _suggest_params(), _suggest_summary(), _suggestion_rows(), _near(), points() (+1 more)

### Community 79 - "Users and Ops Console Design"
Cohesion: 0.22
Nodes (9): Business owner (Connect customer), Fonecta staff (support/ops), Ops console /ops (S1-S5), O2 season card page /c/<token>, cs/audit.py hash-chained audit log, app/app.py Flask routes, cs/guardrails.py (A4/A10), cs/links.py signed single-use tokens (A8) (+1 more)

### Community 80 - "README Chain and Yext Behaviour"
Cohesion: 0.22
Nodes (7): Measured Yext holidayHours API behaviour (list replaces, omitted key keeps, [] clears, past dates dropped), A9 Apply and confirm (Yext holidayHours write, read-back, per-publisher confirmation), Connect Suggests synthetic dataset (3,000 FI/SE/NO businesses, 41,755 target rows, CC BY 4.0), Fonecta Connect Suggests challenge, Fonecta Connect (publishes business info to 35+ channels), Since AI Hackathon 2026 (Turku, 6-8 Nov 2026), Yext sandbox entity SinceAi04 (renamed demo cafe Kahvila Aurora, FI-D0001)

### Community 81 - "Entity Snapshots"
Cohesion: 0.27
Nodes (5): entities(), entity_index(), entity_snapshot_at(), entity_state_known(), test_entities_and_index()

### Community 82 - "Agent Working Agreement"
Cohesion: 0.22
Nodes (8): Working agreement for the build agents, ML agent, Q&A agent (read-only), B10 Business memory graph (Graph RAG), cs/live/calendar.py live calendar adapter (A14), scripts/replay.py pilot-log replay, cs/targets.py days to check (A2), MongoDB Atlas (connect_suggests)

### Community 83 - "LLM Roles and Safety"
Cohesion: 0.29
Nodes (8): L1 Review reader (regex then Opus), L2 Free-text reply parser, L3 Reason-sentence and review-reply polisher (slot check), M5 Closed-for-good scorer, Build plan: Part A (Fonecta requirements), cs/flags.py closed for good (A5 v1), cs/profile/review_reply.py closed-door review replies, Claude Opus (Microsoft Foundry)

### Community 84 - "Season Labels"
Cohesion: 0.31
Nodes (7): _label_view(), _result(), _upcoming(), season_of(), label_for(), test_season_and_cutoffs(), test_label_for_only_at_label_snapshots()

### Community 85 - "Setup Log and Yext Mock"
Cohesion: 0.31
Nodes (7): FONECTA_CONNECT_SUGGESTS_2026 dataset (read-only), cs/sim.py owner simulator, WriteRecord dataclass, cs/yext.py YextClient (A9), Yext mock API (dataset mock_api), Phase 0 setup log (7 Nov 2026), Yext sandbox entity SinceAi04

### Community 86 - "Guardrails, Owner-Wins, Radar Plan"
Cohesion: 0.22
Nodes (6): A10 Guardrails (approval/permission, opt-outs, audit), B10 Business memory graph and grounded chat (Graph RAG), B9 Season radar for Fonecta ops, Fonecta's guidance on Yext data (fixed fields, fixed listings), MongoDB store (audit, permissions, learned facts, kg_nodes/kg_edges), Ops console (queue by stakes, flag queue, audit, CSV export)

### Community 87 - "Builder-ML Contract"
Cohesion: 0.36
Nodes (8): Builder agent, cs/engine/api.py predict() contract, M6 Drift scorer, cs/suggest/baseline.py baseline suggestion engine (A3 v0), cs/display.py shown_hours (A1), cs/profile/drift.py regular-hours drift, Evidence dataclass, Suggestion dataclass

### Community 88 - "No-History and Deliverables"
Cohesion: 0.25
Nodes (8): A12 No-history case, A14 Deliverables (video, README, deck, licences), FI-D0017 (no-history case), README data sources (signal → production source), Bonus: replace a dataset file with a live source, No-history customer case, Optional public data (holiday calendars, LinkedEvents, FMI/SMHI/MET Norway, PRH/Brønnøysund), Submission format (demo video, repo + README, data sources, messages, deck)

### Community 89 - "Mock Yext Server"
Cohesion: 0.39
Nodes (3): _call(), main(), _serve()

### Community 91 - "Build Order and Scorer"
Cohesion: 0.38
Nodes (6): App design: Connect Suggests, M0 Baseline rules (Fonecta), cs/decide.py expected points + flip margin (A4), evaluate.py official scorer, cs/export.py predictions_2026.csv, Baseline reproduced (dev 54.49, harmful 224)

### Community 92 - "Card and Permission States"
Cohesion: 0.33
Nodes (3): Card dataclass, Decision dataclass, cs/learn.py learning (A11)

### Community 93 - "Card Message Fallback"
Cohesion: 0.40
Nodes (5): card_message(), confirmation_message(), _fallback_card(), _hours_text(), business_for()

### Community 95 - "Live Calendar Sources"
Cohesion: 0.67
Nodes (3): holidays Python package (MIT), Live special-day calendar (bonus; reproduces all 244 calendar rows), Nager.Date API (fallback calendar)

## Ambiguous Edges - Review These
- `cs/db.py SQLite store (artifacts/connect.db)` → `Fonecta guidance on Yext data`  [AMBIGUOUS]
  docs/SETUP_LOG.md · relation: conceptually_related_to

## Knowledge Gaps
- **48 isolated node(s):** `Traficom regulation 28 L/2025 (unverified SMS sender blocking)`, `GPT-6 Astra (independent message-quality judge)`, `Finding: half of needed changes invisible to copy-last-year`, `Finding: holidays are linked (one latent habit type per business)`, `Finding: owners accept own-history suggestions 95% vs peer-based 81%` (+43 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 646 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Suggestion` connect `Domain Records` to `Suggestion Engine Adapter`, `Message Bridge and Event Log`, `Flask App and Audit Core`, `Guardrail Rules`, `Season Autopilot`, `Profile CLI`, `Message Slot Checks`, `Decision Flow and Follow-ups`, `Suggestions Interface`, `Predictions CSV Export`, `Season Card Composer`, `Guardrail Tests`, `Review Reply Drafts`, `Profile Drift Suggestions`, `Fonecta Baseline Engine`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Are the 24 inferred relationships involving `Suggestion` (e.g. with `build()` and `save_to_mongo()`) actually correct?**
  _`Suggestion` has 24 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Traficom regulation 28 L/2025 (unverified SMS sender blocking)`, `GPT-6 Astra (independent message-quality judge)`, `Finding: half of needed changes invisible to copy-last-year` to the rest of the system?**
  _48 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Ask Connect Graph RAG` be split into smaller, more focused modules?**
  _Cohesion score 0.05087719298245614 - nodes in this community are weakly interconnected._
- **Why does `save_to_mongo()` connect `Predictions CSV Export` to `MongoDB Access Guard`, `Domain Records`, `Message Bridge and Event Log`, `Flask App and Audit Core`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Should `LLM Clients (Claude, Astra)` be split into smaller, more focused modules?**
  _Cohesion score 0.052982456140350874 - nodes in this community are weakly interconnected._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `cs/db.py SQLite store (artifacts/connect.db)` and `Fonecta guidance on Yext data`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._