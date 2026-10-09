# Car Maintenance & Service Risk Tracker

INF1103 Team Project Part 1 -- Group 7 (P12-G7)

Car owners lose track of when each part of their car needs servicing.
This CLI app lets an owner register one of four supported cars, enter
details from their last workshop invoice and today's odometer reading,
and get an urgency score (1-10) that the AI works out from that car's
owner's manual, plus an overdue report and a recommended service date
for each part. Results are stored
and kept across runs.

---

## How to run it

### 1. One-time setup (Mac)

```bash
cd INF1103-P12-G7                  # the project folder
python3 -m venv .venv              # create a virtual environment
source .venv/bin/activate          # activate it (prompt shows "(.venv)")
pip install -r requirements.txt    # install dependencies
cp .env.example .env               # create your private config file
open -e .env                       # add your Gemini key (and Groq key for the backup)
```

Get a free key at https://aistudio.google.com/apikey. Never commit `.env`
(it's in `.gitignore`).

Windows: use `python` instead of `python3`, `.venv\Scripts\activate`
instead of `source ...`, and `copy` instead of `cp`.

Each new terminal: `cd` into the folder and run `source .venv/bin/activate` again.

### 2. Run the app

```bash
python main.py
```

The four car manuals must be in `manuals/` as PDFs, named as listed in
`manuals/README.md`. Without its manual, a car's parts can't be checked.

### 3. Run the tests (no API key or internet needed)

```bash
python -m pytest -v
```

### 4. Run in Docker (needed for the demo)

Install Docker Desktop and make sure it's running, then:

```bash
docker build -t car-maintenance-tracker .
docker run -it --rm --env-file .env \
  -v "$(pwd)/data:/app/data" \
  car-maintenance-tracker
```

- `docker build` also runs every test, and fails if any test fails.
- `-it` is required because the app reads keyboard input.
- `--env-file .env` passes your API key in; it is never copied into the image.
- `-v .../data` keeps your profiles and records on your Mac, so they survive
  between runs. Without it, data is lost when the container stops.
- The manuals are copied into the image, so rebuild after changing a PDF.

Windows PowerShell: use `${PWD}` instead of `$(pwd)` and `` ` `` instead of `\` for line breaks.

---

## Architecture: the four managers

```
User -> io_manager -> ai_manager -> logic_manager -> data_manager
```

| File | Role | Key functions |
|---|---|---|
| `io_manager.py` | Every `print()`/`input()`. Validates input and re-prompts on bad data. | `prompt_service_details`, `prompt_identity`, `display_record`, `display_list`, `display_result` |
| `ai_manager.py` | Core engine. Every maintenance record goes through Gemini. No business logic. | `build_prompt`, `call_api`, `parse_response`, `validate_response`, `process` |
| `logic_manager.py` | Business rules applied to the AI output. | `evaluate`, `score`, `route`, `is_urgent` |
| `data_manager.py` | JSON persistence: records, user profiles, service schedule. | `save`, `load`, `query`, user CRUD, `get_service_interval` |
| `main.py` | The only file that connects managers. Holds login state. | `build_record`, one `*_flow` function per menu option |

### Where each requirement is met

| Requirement (source) | Where |
|---|---|
| C1: no classes (framework brief) | Checked automatically by `tests/test_constraints.py` |
| C2: AI is the core engine | `main.build_record` sends every record through `ai_manager.process`; `route` depends on the AI's urgency score and confidence |
| C3: structured JSON responses, validated | JSON mode plus `validate_response` (required keys, types, 1-10 range); retries once, then falls back |
| C4: flat files, same output across runs | JSON files in `data/`; `temperature=0` makes the AI's answer repeatable |
| C5: runs in Docker | `Dockerfile`; tests run during the build |
| C6 + DevOps: Git history, CI | `.github/workflows/ci.yml` runs the tests and a Docker build on every push and PR |
| Business rule 1: odometer validation (proposal) | `io_manager.prompt_service_details`: odometer can't be below the last service or the last reading on file |
| Business rule 2: interval from stored manufacturer schedule | `data/cars.json` (one schedule per car) read by `data_manager.get_service_interval` |
| Business rule 3: service date recommendation | `logic_manager.evaluate`: based on part importance, driving pace, odometer and AI urgency |
| Multi-condition rule on AI fields | `route`: urgency >= 8 AND confidence High -> URGENT; same score with lower confidence -> FLAGGED |
| Overdue reporting: "650 km overdue" / "12 days overdue" | `km_overdue` / `days_overdue` (exact arithmetic) plus the AI's `overdue_summary` |
| AI bases urgency and confidence on the car's manual | `ai_manager.process` attaches `manuals/<car>.pdf` to every request; the AI returns `manual_reference` |
| Tests cover logic_manager with hardcoded AI responses, offline | `tests/test_logic_manager.py` (also `test_ai_manager.py`, `test_data_manager.py`) |

### Data files (`data/`)

| File | Contents | In Git? |
|---|---|---|
| `cars.json` | The 4 cars: model, condition, manual PDF, interval per part (`km`, `months`) | Yes |
| `users.json` | Profiles: name, license_plate (key), car_model, car_condition, coe_expiry_date | No (created by the app) |
| `maintenance_records.json` | One entry per check: inputs + AI fields + decision fields + outcome | No (created by the app) |

### Error handling (for the Exception Handling Matrix)

| Scenario | Detected by | Handling | What the user sees |
|---|---|---|---|
| Gemini fails (outage / timeout / rate limit / bad key) | Exception caught in `_call_gemini` | Same prompt sent to the Groq backup, with the manual's text | Normal result; "Answered by: Groq (backup)" |
| Both Gemini and Groq fail | Both calls return None | Logged, retried once, then fallback values | "NEEDS MANUAL REVIEW - AI assessment unavailable" |
| Malformed AI response | `parse_response` / `validate_response` return None | Logged, retried once, then fallback | Same as above |
| Missing data file | `os.path.exists` in `_read_json` | Start with an empty list | Empty history; app continues |
| Corrupt data file | `json.JSONDecodeError` caught | Logged, treated as empty | App continues |
| Missing/corrupt schedule | `load_schedule` | Built-in default schedule used | Parts still listed |
| Invalid user input | Validation in each `io_manager` prompt | Reject and re-prompt | Clear message explaining what's wrong |
| Car manual PDF missing | `data_manager.get_manual_path` returns None | Check is not started | "The manual for ... is missing" |
| No terminal / Ctrl+C | `EOFError` / `KeyboardInterrupt` in `main` | Clean exit | Exit message with the `docker run -it` hint |

---

## Team Git workflow (from the Collaboration and Git slides)

1. Start from an up-to-date `main`: `git checkout main && git pull`
2. One branch per feature: `git checkout -b feature/logic-coe-rule`
3. Small commits with descriptive messages, e.g. `add validate_response with schema check` (not `update`)
4. `git push origin feature/logic-coe-rule`, then open a Pull Request on GitHub
5. CI runs automatically -- merge only when it's green
6. Delete the branch after merging

---

## Before submission (deadline: Wed 14 Oct 2026, 23:59)

- [ ] Copy each car's intervals from its manual into `data/cars.json`, and put the 4 PDFs in `manuals/`
- [ ] Every member runs `docker build` + `docker run -it ...` on their own laptop
- [ ] Engineering report (max 5 pages): data flow diagram + exception handling matrix (tables above are a starting point)
- [ ] Zip named `LabGroup_TeamNumber_ProjectPart1Final` containing: report, code, test script, Git history, Docker image

## Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: pip` / `python` | Use `pip3` / `python3`, or activate the venv first |
| `API key not valid` | Check `.env` has `GEMINI_API_KEY=` followed by the key, no quotes or spaces |
| Model not found error | Add `GEMINI_MODEL=<a model listed in AI Studio>` to `.env` |
| Docker exits straight away | You forgot `-it` |
| Records disappear between Docker runs | You forgot `-v "$(pwd)/data:/app/data"` |