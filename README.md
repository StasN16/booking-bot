# Booking Bot

A WhatsApp assistant that books, moves and cancels appointments for a
clinic, plus a web dashboard where the clinic sees and manages all of it.

- **The bot** answers customers on WhatsApp in Hebrew, English or Russian,
  offers only times that are really free, and books through the same rules
  the dashboard uses.
- **The dashboard** (`/dashboard`) has a calendar, appointments, customers,
  the team and their hours, treatments, statistics and settings. It is in
  Hebrew by default, with English one click away, and works on a phone.
- **Reminders** go out on WhatsApp a day and an hour before each
  appointment.
- **The audit trail** records every conversation step by step in
  `logs/audit.jsonl`. The server checks it every 15 minutes and logs
  anything that went wrong.

Built with FastAPI, PostgreSQL (Supabase in production), GPT-4o and the
Meta WhatsApp Cloud API.

## Setting up a Mac (once)

1. Install Homebrew. Open **Terminal**, paste the command from
   [brew.sh](https://brew.sh), and follow what it prints.
2. Get the code:
   ```bash
   git clone -b claude/exciting-euler-byn7tz https://github.com/StasN16/booking-bot.git
   cd booking-bot
   ```
3. If you have a `.env` file from another computer, copy it into the
   `booking-bot` folder now. Everything already in it is kept.
4. Run the setup:
   ```bash
   ./scripts/setup_mac.sh
   ```
   It installs Python, PostgreSQL and ngrok, creates a local database with
   a demo clinic, and runs the tests. It asks only for what is missing:

   | It asks for | Where to find it |
   |---|---|
   | OpenAI API key | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
   | WhatsApp token | Meta Business settings → System users → your user → Generate token |
   | WhatsApp phone number ID | Meta developer app → WhatsApp → API Setup |
   | WhatsApp verify token | Any word you choose; the same word goes in Meta's webhook settings |
   | Dashboard password | Your choice, or press Enter for a generated one (it is printed) |
   | ngrok authtoken | [dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken) |

   Running it again is safe: finished steps are skipped.

## Every day

```bash
cd booking-bot
./scripts/start.sh
```

This starts the server and the ngrok tunnel WhatsApp reaches it through.
Then open **http://localhost:8000** and sign in with the dashboard
password. `Ctrl+C` in the Terminal stops everything.

If the script says **"The webhook URL changed"**, WhatsApp messages stop
arriving until you paste the new address into Meta: WhatsApp →
Configuration → Callback URL → Edit. Set `NGROK_DOMAIN` in `.env` to your
free fixed ngrok domain and the address never changes again.

Forgot the dashboard password? It is in `.env`, on the `ADMIN_PASSWORD`
line.

## The dashboard

| Page | What it does |
|---|---|
| Calendar | Day (a column per therapist), week or month. Click free time to book there; click an appointment to open it. Bookings made on WhatsApp appear within a minute. |
| Appointments | Every appointment in a range of dates, with filters by status and therapist and a search by name, phone or treatment. |
| Customers | Everyone who has written to the bot or been booked. Each card shows the customer's history and lets you turn their reminders off. |
| Team | Each therapist's working days and hours. Free times, in the bot and on the dashboard, come from these hours. |
| Treatments | What the bot offers, with length and price. Retiring a treatment hides it from new bookings and keeps the history. |
| Statistics | Appointments, revenue, cancellations, and the split by treatment and therapist, for any range up to a year. |
| Settings | The clinic's details, the dashboard language, and signing out. |

Booking from the dashboard offers only the times the bot would offer, so
the two can never double-book each other. Moving or cancelling does not
message the customer. Instead, the dashboard offers a WhatsApp message,
already written, for you to send.

## Settings (`.env`)

`.env` holds this machine's settings and secrets. It is never committed.
`.env.example` lists every key.

| Key | Meaning |
|---|---|
| `DATABASE_URL` | The database. Setup points it at the local one and keeps earlier URLs as comments. |
| `BUSINESS_ID` | Which clinic in the database this server runs. Leave it as the seed data set it. |
| `TIMEZONE` | The clinic's timezone. Every time is shown and booked in it. Defaults to `Asia/Jerusalem`. |
| `OPENAI_API_KEY` | For GPT-4o. |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` | The WhatsApp Cloud API connection. |
| `ADMIN_PASSWORD`, `JWT_SECRET` | Dashboard sign-in. If either is blank, the dashboard stays locked. |
| `JWT_HOURS` | How long a dashboard sign-in lasts. Defaults to 12. |
| `REPLY_DELAY_SECONDS` | A short pause before the bot answers, so it reads as typed. Defaults to 0.5. |
| `NGROK_DOMAIN` | Optional fixed ngrok domain, so the webhook URL never changes. |
| `REMINDERS_ENABLED`, `REMINDER_POLL_MINUTES` | The reminder loop. Defaults to on, every 10 minutes. |
| `AUDIT_WATCH_MINUTES` | How often the server checks its own audit trail. 0 turns the checks off. |

Check that the credentials work, without changing anything:

```bash
poetry run python scripts/configure.py --check
```

## Scripts

| Script | What it does |
|---|---|
| `scripts/setup_mac.sh` | One-time Mac setup. |
| `scripts/start.sh` | Starts the server and the tunnel. |
| `scripts/configure.py` | Fills in missing `.env` values and tests the credentials (`--check` only tests). |
| `scripts/setup_local_db.py` | Creates the local database, applies migrations and adds the demo clinic. |
| `scripts/migrate_supabase.py` | Applies migrations to Supabase without touching `.env`. |
| `scripts/integration_test.py` | Runs the full booking flow and the dashboard API against a real database, then cleans up. |
| `scripts/analyze_audit.py` | Reports problems in the audit trail. `--show TRACE_ID` shows one conversation step by step. |

## Tests

```bash
poetry run pytest                              # unit tests, no database needed
poetry run python scripts/integration_test.py  # against the database in .env
```

## How it is organised

```
app/
  main.py           the server: routes, dashboard files, reminder and audit loops
  api/v1/           webhook (WhatsApp), auth, appointments, customers, therapists,
                    treatments, business: the API the dashboard calls
  services/         conversation (the bot), ai_engine (GPT-4o), booking,
                    availability, reminders, whatsapp, audit analysis
  core/             models, schemas, database, timezone helpers, audit trail
  static/dashboard/ the dashboard: plain JavaScript modules, no build step
migrations/         Alembic database migrations
scripts/            setup, start, checks
tests/              unit tests
```

The bot and the dashboard share one booking service
(`app/services/booking.py`) and one availability calculation
(`app/services/availability.py`), so both follow the same rules.

The dashboard builds everything on the page from plain text, never from
HTML, and the server forbids inline scripts. A customer's name or note can
only ever be displayed, never run.

## When something is wrong

| Problem | What to do |
|---|---|
| The bot does not answer | Is `start.sh` running? Did it warn that the webhook URL changed? Then check the token: `poetry run python scripts/configure.py --check`. |
| `configure.py --check` says Meta rejected the WhatsApp token | Generate a new token for the system user in Meta Business settings and put it in `.env` as `WHATSAPP_TOKEN`. |
| "Port 8000 is already in use" | The bot is already running in another Terminal window. Use that one, or stop it there with `Ctrl+C`. |
| The dashboard says sign-in is not set up | Run `./scripts/setup_mac.sh`, then start the server again. |
| Supabase cannot be reached | Some home networks block port 5432. Use the local database, or a phone hotspot for Supabase work. |
| Something went wrong in a conversation | `poetry run python scripts/analyze_audit.py` lists what failed and where. |

## Known limits and next steps

- **Reminders and WhatsApp's 24-hour rule.** WhatsApp lets a business
  send free text only within 24 hours of the customer's last message.
  Outside that window a reminder needs a message template approved by
  Meta. Without one, reminders to customers who booked days ahead are
  refused. This is planned for before going live (step 12).
- **Turning a customer's reminders off** stops reminders only. The bot
  still answers that customer.
- **Step 11:** syncing appointments to Google Calendar.
- **Step 12:** running on Railway with Supabase, instead of on this Mac.
