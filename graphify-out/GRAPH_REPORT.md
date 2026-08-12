# Graph Report - .  (2026-08-12)

## Corpus Check
- 89 files · ~97,537 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1721 nodes · 3782 edges · 75 communities (65 shown, 10 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 412 edges (avg confidence: 0.57)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- App Window Widgets
- Email & Calendar Providers
- Risk Classification & Skill Base
- Jarvis Main Window
- Screen State Capture
- ODIN HUD Window
- Mouse & Keyboard Input Skills
- HUD Sparkline Weather Tests
- App Entry & Global Hotkey
- Skill Manager Registry
- Speech Output Pipeline
- Web Fetch Skills
- HUD Telemetry Tests
- HUD Radial Gauge Tests
- Email & Calendar Skills
- Brain Test Fixtures
- HUD Widget Render Tests
- Microphone Audio Capture
- Brain Fake Client Tests
- HUD Voice Orb Tests
- Window Control Skills
- GUI Integration Tests
- Brain Error Handling
- HUD Layout & Console
- Barge-In Watcher
- HUD Boot Sequence
- Embedding Backends
- Command Risk Tests
- Trash & Undo Move
- SQLite Store Notes
- LLM Model Dispatch
- HUD Frame Painting
- HUD Bridge Tests
- Orb Render Tests
- HUD Window Zone Tests
- Main CLI Loop
- Research Preflight
- Edge TTS Engine
- Shell Command Skill
- Memory Skill Persistence
- File Delete Skill
- File Read & Search Skills
- Undo Journal Core
- Research Decomposition
- Knowledge Base Retrieval
- HUD Console Overlay
- Brain System Prompt
- HUD Console Input Tests
- File Write Skill
- Speech Input & RMS
- Fake Anthropic Stream
- Voice Orb Painting
- Brain History Persistence
- File Move Skill
- App & Website Launching
- Fake OpenAI Client
- Orb Particle System
- HUD Spectrum Audio
- Undo Journal Expiry
- Screen State Fixtures
- Reminder Skill
- Learning Status Reporting
- Store Recall & Forget
- Recent Message History
- Note Skill
- Message Block Encoding
- Persistence Test Fixtures
- Bridge Speech Test
- Confirmation Timeout Test
- Late Reminder Test
- Notification Retry Test
- Recall Without Query Test
- Tool Result Restore Test

## God Nodes (most connected - your core abstractions)
1. `OdinHudWindow` - 97 edges
2. `SkillManager` - 72 edges
3. `JarvisMainWindow` - 65 edges
4. `BaseSkill` - 64 edges
5. `Risk` - 57 edges
6. `Brain` - 47 edges
7. `get_journal()` - 45 edges
8. `VoiceOrb` - 43 edges
9. `Session` - 35 edges
10. `UiBridge` - 35 edges

## Surprising Connections (you probably didn't know these)
- `_Hotkey` --uses--> `Brain`  [INFERRED]
  app.py → core/brain.py
- `_Hotkey` --uses--> `SpeechOutput`  [INFERRED]
  app.py → core/speech_output.py
- `_Hotkey` --uses--> `Session`  [INFERRED]
  app.py → main.py
- `_Hotkey` --uses--> `OrbWindow`  [INFERRED]
  app.py → ui/app_window.py
- `_Hotkey` --uses--> `OdinHudWindow`  [INFERRED]
  app.py → ui/hud/window.py

## Import Cycles
- None detected.

## Communities (75 total, 10 thin omitted)

### Community 0 - "App Window Widgets"
Cohesion: 0.05
Nodes (43): QDialog, QHBoxLayout, QVBoxLayout, ActionCardWidget, ActivityLogWidget, _Backdrop, _bubble(), _bubble_width_for() (+35 more)

### Community 1 - "Email & Calendar Providers"
Cohesion: 0.07
Nodes (41): default_account(), get_provider(), google_configured(), _google_credentials_path(), google_ready(), _google_token_path(), GoogleProvider, microsoft_configured() (+33 more)

### Community 2 - "Risk Classification & Skill Base"
Cohesion: 0.06
Nodes (38): ABC, _classify_segment(), Risk tiers and shell-command classification. Nothing here ever blocks an…, Risk, IntEnum, BaseSkill, SkillResult, Base class every skill must implement. Adding a new skill is just subclassing… (+30 more)

### Community 3 - "Jarvis Main Window"
Cohesion: 0.07
Nodes (14): JarvisMainWindow, QMainWindow, QWidget, Close out the current streaming bubble so the next narration starts a new one…, Reflect the last turn's token usage in the header chrome. brain may be a test…, Re-fit a bubble already on screen to new text. A streamed reply or a…, The summonable full-screen HUD., Hide the HUD and stop repainting it. The orb stays on the desktop. (+6 more)

### Community 4 - "Screen State Capture"
Cohesion: 0.06
Nodes (53): is_stale(), Shared coordinate mapping between the last screenshot and the real screen.…, Map an (x, y) given in the most recent screenshot's image space to real screen…, Whether the current mapping is old enough that the screen it was read from…, to_real(), ClipboardSkill, _encode_shot(), _ensure_dpi_aware() (+45 more)

### Community 5 - "ODIN HUD Window"
Cohesion: 0.06
Nodes (9): OdinHudWindow, QMainWindow, Boot-time informational messages (restored history, missed reminders, hotkey…, Every dock button, orb launcher, and console submission funnels through here —…, §6.9's `IN 12M` format. Negative deltas (overdue/fired) render as `3M AGO`…, The full-screen instrument HUD., Match the real primary screen instead of the 1920x1080 fallback set at…, §6.8: one row per learned topic, a thin bar for relative size, last-updated… (+1 more)

### Community 6 - "Mouse & Keyboard Input Skills"
Cohesion: 0.06
Nodes (47): ClickSkill, _gui(), PressKeysSkill, (left, top, right, bottom) of the full virtual desktop — spans every monitor,…, Import pyautogui lazily and keep the failsafe on. Returns (module,…, ScrollSkill, TypeTextSkill, _virtual_screen_bounds() (+39 more)

### Community 7 - "HUD Sparkline Weather Tests"
Cohesion: 0.07
Nodes (31): ui/hud/sparkline.py and ui/hud/weather.py., test_fetch_weather_parses_a_well_formed_response(), test_fetch_weather_returns_none_on_malformed_json(), test_fetch_weather_returns_none_on_network_failure(), test_sparkline_caps_its_rolling_window(), test_sparkline_push_updates_the_scale_target(), test_sparkline_renders_with_zero_one_and_many_samples(), ConfirmationBannerWidget (+23 more)

### Community 8 - "App Entry & Global Hotkey"
Cohesion: 0.07
Nodes (29): _Hotkey, main(), _place_orb(), QObject, Desktop entry point: the orb and the HUD. Run: python app.py (or: python…, Global hotkey support, if the optional `keyboard` package is installed. Its…, Bottom-right of the primary screen, clear of the taskbar., ensure_dirs() (+21 more)

### Community 9 - "Skill Manager Registry"
Cohesion: 0.07
Nodes (33): Anthropic shape: local skills first, then server tools. Order is deterministic…, Expose local skills formatted as OpenAI tools., Run a local skill. is_error tells the model the call failed so it can adapt,…, SkillManager, CloseAppSkill, chrome.exe' -> 'chrome, _stem(), CalculatorSkill (+25 more)

### Community 10 - "Speech Output Pipeline"
Cohesion: 0.08
Nodes (31): Queued speaker. `say()` returns immediately; `wait()` blocks until the backlog…, Print immediately, queue for speech. Non-blocking., Block until everything queued so far has been spoken., Whether the worker is actively playing or has a backlog queued., Cut off whatever is currently playing and drop anything still queued. Used for…, SpeechOutput, _Listener, parametrize (+23 more)

### Community 11 - "Web Fetch Skills"
Cohesion: 0.07
Nodes (29): HTMLParser, _decode_response(), _html_text(), _is_public_url(), Raw DuckDuckGo search results: [{title, url, snippet}]. Raises RuntimeError…, Fetch readable text from a public URL without opening a browser., Decode a fetched page's bytes to text. requests defaults response.encoding to…, Refuse loopback, private, and unresolved hosts before an HTTP request. (+21 more)

### Community 12 - "HUD Telemetry Tests"
Cohesion: 0.08
Nodes (30): _counters(), _disk_io(), ui/hud/telemetry.py — TelemetryWorker._collect(), tested directly (not through…, Every thermal field must come back None (rendered as "--"), never a fabricated…, psutil.Process.cpu_percent(None) only reports a real number when called…, test_aggregate_cores_buckets_above_16(), test_aggregate_cores_passes_through_at_or_below_16(), test_disk_io_sample_diffs_and_never_goes_negative() (+22 more)

### Community 13 - "HUD Radial Gauge Tests"
Cohesion: 0.07
Nodes (33): QFont, ui/hud/radial_gauge.py — arc math, threshold recolor, eased transitions., _render(), test_critical_value_starts_a_pulse_timer(), test_renders_at_every_percent_without_raising(), test_set_percent_none_targets_zero_and_shows_dashes(), test_set_percent_targets_the_right_fraction(), test_threshold_recolor_matches_tokens() (+25 more)

### Community 14 - "Email & Calendar Skills"
Cohesion: 0.09
Nodes (27): get_journal(), CreateEventSkill, DeleteEventSkill, ListEventsSkill, Email and calendar skills, backed by core.email_providers. Each account needs…, ReadEmailSkill, SendEmailSkill, fake_provider() (+19 more)

### Community 15 - "Brain Test Fixtures"
Cohesion: 0.13
Nodes (39): make_brain(), Build a Brain wired to a scripted fake client., response(), text_block(), tool_use_block(), Tests for the conversation loop. The headline test is…, A failing skill must come back with is_error so the model can adapt., A malformed tool argument that blows up risk_for() (e.g. a None where a path… (+31 more)

### Community 16 - "HUD Widget Render Tests"
Cohesion: 0.08
Nodes (21): QAbstractButton, ui/hud/widgets.py — Panel, Readout, BarMeter, DockButton, TickRuler., _render(), test_bar_meter_handles_none_fraction_as_zero(), test_bar_meter_peak_tracks_and_decays(), test_dock_button_emits_clicked(), test_dock_button_hover_and_focus_paint_without_raising(), test_panel_body_layout_accepts_children() (+13 more)

### Community 17 - "Microphone Audio Capture"
Cohesion: 0.09
Nodes (23): _import_numpy(), _import_sounddevice(), Microphone, MicrophoneUnavailable, RuntimeError, Shared microphone capture. Both the wake-word detector and speech-to-text read…, Raised when no usable input device exists (or deps are missing)., A single shared input stream. Consumers pull frames off their own queue, so the… (+15 more)

### Community 18 - "Brain Fake Client Tests"
Cohesion: 0.11
Nodes (30): Brain, Convert an Anthropic tool_result payload for an OpenAI-shaped request. Returns…, _split_tool_result(), FakeClient, FakeOpenAIClient, openai_chunk(), Test fakes for the Anthropic streaming client. These let the whole brain loop…, Build one streamed chat.completions chunk. tool_call is (name, json_arguments)… (+22 more)

### Community 19 - "HUD Voice Orb Tests"
Cohesion: 0.08
Nodes (15): ui/hud/voice_orb.py — every state renders, error flash is transient, launcher-…, _render(), test_advance_freezes_rings_while_flashing(), test_click_outside_the_launcher_band_emits_nothing(), test_every_state_renders_without_raising(), test_flash_error_is_transient(), test_status_changed_emits_on_state_and_subtopic_change(), test_unknown_state_falls_back_to_idle() (+7 more)

### Community 20 - "Window Control Skills"
Cohesion: 0.08
Nodes (25): close_handle(), CloseWindowSkill, enumerate_windows(), focus_handle(), FocusWindowSkill, foreground_handle(), _match(), Window management via ctypes against user32. ctypes rather than pywin32: this… (+17 more)

### Community 21 - "GUI Integration Tests"
Cohesion: 0.07
Nodes (25): mock_brain(), mock_session(), fixture, HUD tests. Run headless via Qt's offscreen platform, so they work in WSL. The…, Regression: only the Send button used to be disabled, so pressing Enter started…, Regression: every sentence spoken across an entire multi-tool-call turn used to…, If the model calls a tool with no narration first, the '…' placeholder must not…, Regression: if the turn's last step was a tool call, _live_label was already… (+17 more)

### Community 22 - "Brain Error Handling"
Cohesion: 0.10
Nodes (26): BrainError, _confirm_always(), friendly_error(), _ignore_action(), _ignore_tool_activity(), Exception, The 'brain': sends user input to the configured model, lets it call skills as…, Raised for API failures that already carry a user-facing message. (+18 more)

### Community 23 - "HUD Layout & Console"
Cohesion: 0.16
Nodes (9): QGridLayout, QLabel, place(), QWidget, Grid geometry — the native-Qt transcription of ODIN-HUD.md §4. The zone table…, Add `widget` at `zone`'s position, per the §4 table, in one call — so…, Panel, The bracket-frame workhorse (§5.1): sharp rectangle, corner ticks, a titled… (+1 more)

### Community 24 - "Barge-In Watcher"
Cohesion: 0.12
Nodes (19): BargeInWatcher, make_watcher(), Barge-in: let the user interrupt Jarvis mid-sentence by talking over it. Unlike…, Watches the shared microphone for sustained speech while active. start()/stop()…, Build a watcher, or None if there's no microphone to watch. Never raises:…, _FakeMic, Tests for core/barge_in.py's sustained-energy interrupt watcher. No real audio…, levels maps a sentinel block value to the RMS level a faked rms() should report… (+11 more)

### Community 25 - "HUD Boot Sequence"
Cohesion: 0.09
Nodes (12): _BootCover, QWidget, Boot sequence — ODIN-HUD.md §8. A black cover with one expanding hairline that…, `window`: the OdinHudWindow, already shown full-screen. Keeps its own animation…, run_boot_sequence(), ODIN CONSOLE — ODIN-HUD.md §6.10's typed-input overlay, the replacement for the…, The ODIN instrument HUD — native PyQt6, built to the spec in ODIN-HUD.md.…, RadialGauge — ODIN-HUD.md §5.2: the four gauges flanking the orb, reused… (+4 more)

### Community 26 - "Embedding Backends"
Cohesion: 0.12
Nodes (21): available(), get_embedder(), Shared sentence-embedding model. Used by core.knowledge (deep_learn's RAG…, The shared SentenceTransformer instance, loaded on first use., Whether sentence-transformers is installed, without loading the (slow,…, available(), _get_collection(), index() (+13 more)

### Community 27 - "Command Risk Tests"
Cohesion: 0.14
Nodes (22): classify_command(), is_sensitive_path(), True for drive roots and OS directories. Sensitive paths are not blocked — they…, Assess a shell command. Order matters. The denylist is matched against the…, parametrize, Tests for the risk tiers and the shell-command classifier. The classifier is…, The classifier must match the whole string BEFORE splitting on chain operators.…, Chained commands are harder to read at a glance, so they never run silently… (+14 more)

### Community 28 - "Trash & Undo Move"
Cohesion: 0.10
Nodes (16): move_to_trash(), Path, Copy src into a fresh trash bucket and return the backup path. This copies…, trash_dir(), journal(), fixture, Tests for the undo journal and the trash used by destructive file ops., A bare drive root ('C:/') has Path.name == "" — `bucket / ""` is a no-op join,… (+8 more)

### Community 29 - "SQLite Store Notes"
Cohesion: 0.11
Nodes (8): Store a durable fact. Returns False if it was already known., Upsert a topic's manifest row. chunk_count is the running total stored in the…, One-time import of the old data/notes.txt into the notes table., Store, Row, A stale .migrated marker existing alongside notes.txt (e.g. a restored backup…, test_legacy_notes_file_is_migrated(), test_migration_marker_prevents_reimport()

### Community 30 - "LLM Model Dispatch"
Cohesion: 0.10
Nodes (9): _drain_sentences(), Fetch a few durable facts for the current request only. This is deliberately…, Fetch deep_learn notes relevant to the current request only. Mirrors…, Best-effort text extraction from a message's content, for feeding the…, Render order is tools -> system -> messages, so a breakpoint on the last system…, Build the reasoning-effort kwarg in whatever shape the endpoint actually…, Send the request, dropping the reasoning kwarg if this model rejects it…, _tool_result() (+1 more)

### Community 31 - "HUD Frame Painting"
Cohesion: 0.14
Nodes (6): QPainterPath, QPen, _corner_ticks(), QColor, QPainter, Small L-bracket ticks inset from each corner — the instrument-panel signature…

### Community 32 - "HUD Bridge Tests"
Cohesion: 0.12
Nodes (15): ui/workers.py's UiBridge — the HUD-only signal additions: skill_logged…, test_deep_learn_failure_does_not_emit_kb_changed(), test_deep_learn_success_emits_kb_changed(), test_learning_status_report_reaches_the_bridge_when_wired(), test_other_skills_do_not_emit_kb_changed(), test_report_learning_progress_re_emits_as_a_signal(), test_skill_logged_carries_a_duration(), test_skill_logged_marks_errors() (+7 more)

### Community 33 - "Orb Render Tests"
Cohesion: 0.12
Nodes (9): The whole point of the swarm: tight when idle, scattered when working., paintEvent runs a lot of geometry; a crash in it takes the HUD down., test_orb_renders_in_every_state(), test_orb_swarm_reacts_to_state(), QWidget, setter, Halt the animation. Called when the orb is hidden so an idle Jarvis isn't…, The orb itself. Set .state to one of STATE_STYLE to change its mood. (+1 more)

### Community 34 - "HUD Window Zone Tests"
Cohesion: 0.10
Nodes (5): mock_brain(), mock_session(), fixture, ui/hud/window.py's OdinHudWindow — construction, dock dispatch, the DANGEROUS-…, window()

### Community 35 - "Main CLI Loop"
Cohesion: 0.11
Nodes (9): The on_text callback given to Brain. Starts the barge-in watcher on the first…, Runs on the watcher's own thread. Cuts Jarvis off immediately, then captures…, Return the next utterance, or None if nothing usable was captured., Ask before running a destructive skill. Defaults to NO — a misheard 'shut down'…, Report a completed MODERATE action. Only offers undo when the action genuinely…, Owns the run loop's mutable state: mode, microphone, wake word. The voice path…, Session, test_speak_starts_the_barge_in_watcher_and_still_speaks() (+1 more)

### Community 36 - "Research Preflight"
Cohesion: 0.14
Nodes (18): _llm_complete(), preflight(), One-off, non-streaming completion using whatever provider Jarvis itself is…, Return a user-facing reason deep_learn can't run, or None if it can., Replace the process-wide Store (used by tests)., set_store(), Tests for core/research.py, the pipeline behind deep_learn. Web search and the…, decompose -> research each subtopic (search + synthesize) -> gap-check ->… (+10 more)

### Community 37 - "Edge TTS Engine"
Cohesion: 0.13
Nodes (11): EdgeEngine, _make_engine(), Text-to-speech. Speech runs on a background thread fed by a queue, so the brain…, Microsoft Edge neural voices. Free, no key, needs network., Offline Windows SAPI5 voices via pyttsx3., Build the best available engine, or None to run silent. 'auto' prefers edge-tts…, SapiEngine, No pygame, no pyttsx3, no network: Jarvis must still work in text. (+3 more)

### Community 38 - "Shell Command Skill"
Cohesion: 0.17
Nodes (14): RunCommandSkill, Tests for shell execution. run_command is the one place in this project that…, A shell command cannot be reversed, so it must not offer undo., A disabled capability must not appear in the tool list at all, so the model…, test_captures_stderr(), test_consequence_quotes_the_command(), test_empty_command_is_rejected(), test_kill_switch_removes_the_tool() (+6 more)

### Community 39 - "Memory Skill Persistence"
Cohesion: 0.16
Nodes (13): MemorySkill, Tests for the SQLite store, durable reminders, and memory skills., Guard against wiping everything on a vague 'forget it'., A literal '%' or '_' in a query must not act as a SQL LIKE wildcard — otherwise…, test_future_reminders_do_not_fire_early(), test_memory_deduplicates(), test_memory_forget_escapes_like_wildcards(), test_memory_forget_requires_a_target() (+5 more)

### Community 40 - "File Delete Skill"
Cohesion: 0.18
Nodes (9): Drop one memory from the index. Silently does nothing on any error — forget()…, remove(), _blank(), DeleteFileSkill, _looks_binary(), Path, test_delete_is_dangerous(), test_delete_missing_file_records_no_undo() (+1 more)

### Community 41 - "File Read & Search Skills"
Cohesion: 0.21
Nodes (15): ListDirSkill, ReadFileSkill, SearchFilesSkill, Tests for filesystem skills., test_blank_path_is_rejected_for_read_only_skills(), test_list_dir(), test_read_file_is_safe_tier(), test_read_file_missing_path() (+7 more)

### Community 42 - "Undo Journal Core"
Cohesion: 0.17
Nodes (13): Undo journal and the trash that makes file deletion reversible. In-memory only:…, Replace the process-wide journal (used by tests)., set_journal(), UndoEntry, UndoJournal, journal(), journal(), fixture (+5 more)

### Community 43 - "Research Decomposition"
Cohesion: 0.20
Nodes (14): _decompose(), _dedupe(), _find_gaps(), _parse_json_list(), Exception, Agentic research pipeline behind the deep_learn skill. topic -> subtopics ->…, What a mastery quiz would ask, filtered down to what's NOT already well covered…, Raised for a condition that should be shown to the user verbatim. (+6 more)

### Community 44 - "Knowledge Base Retrieval"
Cohesion: 0.22
Nodes (12): available(), best_distance(), chunk_text(), _get_collection(), query(), Local long-term knowledge store for the deep_learn skill (RAG). This is…, Retrieve the most relevant stored chunks for a query. Returns [] rather than…, Distance of the single closest stored chunk, or infinity if none. (+4 more)

### Community 45 - "HUD Console Overlay"
Cohesion: 0.22
Nodes (5): QKeyEvent, ConsoleOverlay, QObject, QWidget, `submitted` fires for ordinary text (routed to Brain.ask()); `slash_command`…

### Community 46 - "Brain System Prompt"
Cohesion: 0.17
Nodes (10): build_system_prompt(), Assemble the system prompt from the tools this build actually has. Everything…, client: an openai.OpenAI or mock client (injected for testing) confirm:…, Tool names given to the model., SERVER_TOOLS are Anthropic-only. On an OpenAI-compatible endpoint the model…, A prompt that varies between turns invalidates the whole cached prefix., test_prompt_describes_web_search_when_present(), test_prompt_is_stable_for_the_same_tool_set() (+2 more)

### Community 47 - "HUD Console Input Tests"
Cohesion: 0.41
Nodes (11): QMouseEvent, QPoint, QPointF, _move(), _press(), ui/hud/console.py's ConsoleOverlay — title-band dragging. ConsoleOverlay is a…, _release(), test_click_below_the_title_band_does_not_start_a_drag() (+3 more)

### Community 48 - "File Write Skill"
Cohesion: 0.17
Nodes (11): WriteFileSkill, parametrize, Path("").expanduser() silently resolves to the process's own working directory…, write_text() opens in "w" mode, which truncates the file immediately — before a…, test_blank_path_is_rejected_rather_than_resolving_to_cwd(), test_overwrite_failure_after_truncation_still_leaves_a_usable_undo(), test_overwrite_is_dangerous(), test_overwrite_then_undo_restores_original_bytes() (+3 more)

### Community 49 - "Speech Input & RMS"
Cohesion: 0.22
Nodes (5): Root-mean-square level of an int16 block, normalised to roughly 0..1., rms(), Record until the user stops talking, then transcribe. Returns '' if nothing…, Adaptive gate. Tracks the ambient floor while nothing is being said, so a noisy…, SpeechInput

### Community 50 - "Fake Anthropic Stream"
Cohesion: 0.18
Nodes (3): FakeMessages, _FakeStream, Replays a scripted list of responses. An entry that is an Exception is raised…

### Community 52 - "Brain History Persistence"
Cohesion: 0.20
Nodes (6): _is_plain_user_turn(), Run one full turn. self.history is left untouched if this raises., Restore the tail of the previous session. Returns messages loaded., Write a completed turn to disk. Only called after the turn committed, so what…, Return a bounded, protocol-valid request history. A tool_result refers to a…, Whether a message is a safe start point after history trimming.

### Community 53 - "File Move Skill"
Cohesion: 0.20
Nodes (8): MoveFileSkill, Moving into a folder nests the file inside it — nothing is replaced, so this…, Moving INTO a sensitive root is dangerous; moving FROM a location that merely…, test_move_destination_sensitivity_checks_the_destination(), test_move_into_existing_directory_is_only_moderate(), test_move_into_existing_directory_then_undo(), test_move_onto_existing_is_dangerous(), test_move_then_undo_returns_the_file()

### Community 54 - "App & Website Launching"
Cohesion: 0.20
Nodes (8): Check common Windows install paths for applications like Opera GX, Opera, Brave., _resolve_windows_app_executable(), Find a specific browser's executable so a URL can be handed to it as an…, Normalise user/model input to an http(s) URL, or None if it isn't one. The…, Look up an executable's install path via the Windows 'App Paths' key, which…, _registry_app_path(), _resolve_browser_executable(), _to_web_url()

### Community 55 - "Fake OpenAI Client"
Cohesion: 0.25
Nodes (5): Block, _FakeCompletions, SimpleNamespace, Stands in for an SDK content block (TextBlock / ToolUseBlock)., gui()

### Community 56 - "Orb Particle System"
Cohesion: 0.32
Nodes (4): Random, _Particle, The arc reactor: a hand-painted orb that shows what Jarvis is doing. Everything…, One mote in the swarm. Radius eases toward a per-state target rather than…

### Community 59 - "Screen State Fixtures"
Cohesion: 0.33
Nodes (6): clear(), Reset hook, mainly for tests., fixture, qapp(), screen_state's last-screenshot mapping is module-global; without this a mapping…, _reset_screen_state()

### Community 60 - "Reminder Skill"
Cohesion: 0.33
Nodes (5): ReminderSkill, The old version used a daemon threading.Timer, which vanished on exit., test_list_reminders(), test_reminder_is_persisted_not_a_timer(), test_reminder_rejects_bad_input()

### Community 61 - "Learning Status Reporting"
Cohesion: 0.50
Nodes (4): Structured deep_learn progress: topic, current subtopic, and a 0..1 fraction —…, report(), set_callback(), test_learning_status_report_is_a_no_op_without_a_callback()

### Community 62 - "Store Recall & Forget"
Cohesion: 0.40
Nodes (3): _escape_like(), Semantic search first when a query is given, falling back to the LIKE search…, Escape SQL LIKE wildcards so a literal '%' or '_' in a memory query matches…

### Community 63 - "Recent Message History"
Cohesion: 0.50
Nodes (3): _is_plain_user_turn(), True for a user message that is ordinary text — i.e. a safe place to start a…, Return the last `limit` messages, oldest first, trimmed to start on a user turn…

### Community 64 - "Note Skill"
Cohesion: 0.50
Nodes (3): NoteSkill, test_notes_roundtrip(), test_notes_survive_a_new_store()

### Community 66 - "Persistence Test Fixtures"
Cohesion: 0.67
Nodes (3): fixture, A real Store on a temp file, installed as the process-wide singleton., store()

## Knowledge Gaps
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_journal()` connect `Email & Calendar Skills` to `App Window Widgets`, `Risk Classification & Skill Base`, `Jarvis Main Window`, `ODIN HUD Window`, `Mouse & Keyboard Input Skills`, `HUD Sparkline Weather Tests`, `App Entry & Global Hotkey`, `Skill Manager Registry`, `Brain Test Fixtures`, `Window Control Skills`, `HUD Bridge Tests`, `Main CLI Loop`, `Shell Command Skill`, `File Delete Skill`, `File Read & Search Skills`, `Undo Journal Core`, `File Write Skill`, `File Move Skill`, `Undo Journal Expiry`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Why does `Brain` connect `Brain Fake Client Tests` to `Risk Classification & Skill Base`, `Main CLI Loop`, `App Entry & Global Hotkey`, `Skill Manager Registry`, `Brain System Prompt`, `Fake Anthropic Stream`, `Brain History Persistence`, `Brain Error Handling`, `Fake OpenAI Client`, `LLM Model Dispatch`?**
  _High betweenness centrality (0.104) - this node is a cross-community bridge._
- **Why does `OdinHudWindow` connect `ODIN HUD Window` to `App Window Widgets`, `HUD Bridge Tests`, `HUD Window Zone Tests`, `HUD Sparkline Weather Tests`, `App Entry & Global Hotkey`, `HUD Telemetry Tests`, `HUD Radial Gauge Tests`, `HUD Console Overlay`, `HUD Widget Render Tests`, `Speech Input & RMS`, `HUD Voice Orb Tests`, `HUD Layout & Console`, `HUD Boot Sequence`?**
  _High betweenness centrality (0.087) - this node is a cross-community bridge._
- **Are the 23 inferred relationships involving `OdinHudWindow` (e.g. with `_Hotkey` and `ConfirmationBannerWidget`) actually correct?**
  _`OdinHudWindow` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 47 inferred relationships involving `SkillManager` (e.g. with `Brain` and `BrainError`) actually correct?**
  _`SkillManager` has 47 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `JarvisMainWindow` (e.g. with `ReactorOrb` and `KnowledgeDialog`) actually correct?**
  _`JarvisMainWindow` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 45 inferred relationships involving `BaseSkill` (e.g. with `Risk` and `CreateEventSkill`) actually correct?**
  _`BaseSkill` has 45 INFERRED edges - model-reasoned connections that need verification._