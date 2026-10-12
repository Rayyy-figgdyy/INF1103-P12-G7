# Car Maintenance & Service Risk Tracker

INF1103 Team Project Part 1, Group 7 (P12 G7)

## Why we built this

Most car owners only know a part needs servicing when the workshop tells them,
or when something starts making a strange noise. The owner's manual tells you
how often each part should be serviced, but nobody reads a 600 page PDF.

So we built a small command line app that reads the manual for you. You pick
your car, type in a few numbers from your last workshop invoice, and the app
tells you how urgently that part needs attention, whether it's overdue, and
when you should book it in.

Our test setup has four cars: a brand new Honda Civic 2025, and three used
cars, a Mazda 3 Hatchback 2020, a Hyundai Elantra 2021 and a Kia Forte.

You can check six parts on each car: Engine Oil, Brake Pads, Spark Plugs,
Tyres, Engine Air Filter and Coolant.

## What using it looks like

1. Register with your name, licence plate, car and COE expiry date (or log in
   if you've used it before).
2. Choose Check a part and pick one, for example Brake Pads.
3. Type the date and odometer reading from your last service, plus today's
   odometer reading. Dates are always DD/MM/YYYY.
4. The app sends this, together with your car's manual, to the AI. The AI
   gives an urgency score from 1 to 10 and says how confident it is.
5. Our own rules then turn that into an outcome you can act on, such as
   URGENT or OK, along with a "service by" date and a risk score out of 100.

Every check is saved, so you can come back later to look at your history or
just the things that need attention. If you enter exactly the same check
twice, the app won't ask the AI again. It just tells you
"This is repeated information" and shows the earlier result.


## Getting it running

### First time only (Mac)

```bash
cd INF1103-P12-G7                  # go into the project folder
python3 -m venv .venv              # make a virtual environment
source .venv/bin/activate          # turn it on; your prompt should now show (.venv)
pip install -r requirements.txt    # install the libraries we use
```

Next, create a file called `.env` in the project folder with your API keys:

```
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
```

You can get a free Gemini key at https://aistudio.google.com/apikey and a free
Groq key at https://console.groq.com/keys. Groq is only our backup for when
Gemini is down, so the app still runs without it. Please never commit
`.env` or paste your keys anywhere. It's already in `.gitignore`.

On Windows, use `python` instead of `python3`, `.venv\Scripts\activate`
instead of the `source` line, and create `.env` in Notepad.

Each time you open a new terminal, `cd` into the folder and run
`source .venv/bin/activate` again.

### Running the app

```bash
python main.py
```

The four manuals need to be in the `manuals` folder (they already are in the
repo). If a car's manual is missing, you won't be able to check that car's
parts.

### Running the tests

```bash
python -m pytest -v
```

The tests don't need an API key or internet. Instead of calling the AI, they
use fake, hardcoded AI answers.

### Running it in Docker (this is what we'll demo)

Open Docker Desktop first and wait until it says it's running. Then:

```bash
docker build -t car-maintenance-tracker .
docker run -it --rm --env-file .env \
  -v "$(pwd)/data:/app/data" \
  car-maintenance-tracker
```

A few things that caught us out:

1. `docker build` runs all the tests as well. If one test fails, the build fails.
2. Don't leave out `-it`. The app needs a keyboard, so without it, it exits
   straight away.
3. `--env-file .env` hands your keys to the app while it runs. The keys are
   never copied into the image.
4. `-v "$(pwd)/data:/app/data"` saves your profiles and checks on your own
   computer. Without it, everything disappears when the container stops.
5. The manuals are copied into the image when it's built, so if you change
   a PDF, build again.

On Windows PowerShell, use `${PWD}` instead of `$(pwd)`, and a backtick
`` ` `` instead of `\` at the end of each line.


## How the code is organised

We followed the four manager structure from the course. Information flows in
one direction:

```
You → io_manager → ai_manager → logic_manager → data_manager
```

io_manager.py handles everything you see and type. All `print()` and
`input()` calls live here and nowhere else. It also checks your input and asks
again if something doesn't make sense.

ai_manager.py builds the question for the AI, attaches the car's manual,
sends it to Gemini (or to Groq if Gemini fails), and checks that the answer is
valid JSON in the format we asked for. It makes no decisions itself.

logic_manager.py holds our business rules. It takes the AI's answer and
works out whether the part is overdue, the risk score, the "service by" date,
and the final outcome.

data_manager.py reads and writes the JSON files in the `data` folder:
profiles, saved checks and each car's service schedule.

main.py connects the four managers and runs the menu. It's the only file
that talks to more than one manager.

### Where to find each requirement

1. C1, no classes. Only functions are used, and `tests/test_constraints.py`
   checks this automatically.
2. C2, AI is the core engine. `main.build_record` sends every check
   through `ai_manager.process`, and the outcome depends on the AI's urgency
   score and confidence.
3. C3, structured JSON from the AI. We ask for JSON only, then
   `validate_response` checks the keys, the types and that the score is
   between 1 and 10. If the answer is bad, it retries once and then falls
   back safely.
4. C4, flat files with the same results each run. Everything is stored as
   JSON in the `data` folder. The AI runs at temperature 0 so its answers stay
   consistent.
5. C5, runs in Docker. See the `Dockerfile`. The tests run during the build.
6. C6, Git history. Small commits, one feature per branch (see below).
7. Rule 1, the odometer can't go backwards.
   `io_manager.prompt_service_details` won't accept a reading lower than the
   last service or the last reading on file.
8. Rule 2, service intervals come from the manufacturer. `data/cars.json`
   holds each car's schedule, read by `data_manager.get_service_interval`.
9. Rule 3, recommended service date. `logic_manager.evaluate` uses the AI's
   urgency, how fast you drive, and whether the part is safety critical
   (brake pads and tyres).
10. A rule that combines AI fields. In `route`, urgency 8 or more and
    High confidence gives URGENT. The same score with lower confidence gives
    FLAGGED, so a mechanic can double check.
11. Overdue reporting. `km_overdue` and `days_overdue` are calculated
    exactly, and the AI adds a one line `overdue_summary` such as
    "Engine oil: 1,500 km overdue".
12. The AI uses the car's manual. Every request includes the car's PDF from
    the `manuals` folder, and the AI says where in the manual it found the
    answer (`manual_reference`).
13. Tests use hardcoded AI responses. See `tests/test_logic_manager.py`,
    plus tests for the other managers.

### The data files

cars.json has the 4 cars, their manuals, and how often each part needs
servicing (km and months, whichever comes first). This one is in Git.

users.json has everyone's profile: name, licence plate, car and COE
expiry. The app creates it, so it isn't in Git.

maintenance_records.json has every check: what was typed, what the AI
said, and what our rules decided. The app creates it, so it isn't in Git.

### When things go wrong

We wanted the app to keep going, not crash. Here's how each problem is handled:

1. Gemini is down, busy or rejects the key. `call_gemini` catches the
   error and the same question goes to Groq, with the relevant parts of the
   manual. You still get a normal result, marked "Answered by: Groq".
2. Gemini and Groq both fail. The app waits a few seconds and tries once
   more, then uses safe default values. You see
   "NEEDS MANUAL REVIEW, AI assessment unavailable".
3. The AI's answer is broken or incomplete. `parse_response` and
   `validate_response` reject it. The app tries once more, then uses the safe
   default, same as above.
4. A data file is missing. `read_json` finds no file, so the app starts
   with an empty list. Your history is empty but everything else works.
5. A data file is corrupted. The JSON error is caught and logged, and the
   file is treated as empty. The app keeps running.
6. You type something invalid. Each prompt in `io_manager` checks your
   answer, explains what's wrong and asks again.
7. A car's manual is missing. `data_manager.get_manual_path` returns
   nothing, so the check doesn't start, and you're told the manual is missing.
8. Ctrl+C, or Docker run without `-it`. `main` catches `KeyboardInterrupt`
   or `EOFError` and exits cleanly with a goodbye message and the
   `docker run -it` hint.


## How we work together in Git

This is the workflow from the Collaboration and Git slides:

1. Get the latest `main` first: `git checkout main && git pull`
2. Make a branch for what you're working on: `git checkout -b feature/tyre-rule`
3. Commit little and often, with messages that say what changed, for example
   `add tyres to safety critical parts` (not just `update`)
4. Push it with `git push origin feature/tyre-rule`, and open a Pull Request
5. Get someone else to look at it, then merge
6. Delete the branch once it's merged


## If something isn't working

1. `command not found: pip` or `python`. Use `pip3` or `python3`, or turn
   on the venv first.
2. `API key not valid`. Open `.env` and check the line is
   `GEMINI_API_KEY=` followed by your key, with no quotes or spaces.
3. A "model not found" error. Add `GEMINI_MODEL=` plus a model name from
   AI Studio to `.env`.
4. Docker closes immediately. You probably left out `-it`.
5. Your checks vanish between Docker runs. You probably left out
   `-v "$(pwd)/data:/app/data"`.
6. `failed to connect to the docker API`. Docker Desktop isn't open. Start
   it and wait a moment.
7. Results flash past and disappear. They're still there. The app now
   waits for you to press Enter before showing the menu again.