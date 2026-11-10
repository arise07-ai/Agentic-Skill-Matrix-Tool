# AI-Powered Skill Matrix App

A web app that standardizes employee skill data, predicts skill gaps, suggests project teams, and builds personalized learning paths.
Users are project managers, team leaders, HR managers and admins.

**Stack:** Next.js (UI) · FastAPI (API) · SQLite (data) · Chroma (vector search) · an LLM provider (OpenAI models).

---

## 1. Design principle: AI proposes, code verifies, a person approves

The model does language and judgement work. Code owns every fact. A person approves anything that changes the matrix.

| Who | Does what |
| --- | --- |
| **The model** | Reads messy files, maps skill names, extracts requirements, estimates risk, chooses learning resources, writes short texts. |
| **Code** | Ratings, hours, schedules, eligibility, seniority, and the checks on everything the model returns. |
| **A person** | Approves changes through the approval queue, and may edit learning plans. |

A rejected or failed model output never reaches the user: the app falls back to a rule-based result (or shows only the computed numbers). The app still works with no model configured; AI features then use their heuristic fallback.

---

## 2. Skill levels (0–4)

| Level | Name |
| --- | --- |
| 0 | None |
| 1 | Learned |
| 2 | Applied |
| 3 | Experienced |
| 4 | Mastered |

- A skill rated 0 is not stored (a missing skill and a 0 mean the same).
- Project requirements use levels 1–4 (default 2).
- A rating outside 0–4 (for example 5, or `4/5` from the old scale) is **not guessed**: the skill is kept without a rating and a warning is shown.
- Colours in the matrix reuse the existing CSS ramp `r1`–`r5` as level + 1.

---

## 3. Features

### Upload (Excel `.xlsx/.xlsm`, PDF)
- Reads employees with name, number, roles, skills, ratings, background and optional domain tags.
- Column mapping, sheet and layout (one row per employee, or per employee-skill) can be corrected; the preview shows warnings.
- Each employee gets a **Junior / Mid / Senior** label computed from the skill levels.
- Import modes: merge (update existing, add new) or replace.

### Single CV (PDF or Word `.docx`)
1. Text is read from the file (`.docx` from its XML; DOCTYPE/ENTITY XML is refused).
2. The model estimates skill levels (maximum 3) and quotes the sentence it used.
3. Code checks each quote is in the CV. An unverified quote sets the level to 1 and flags it; a skill the CV never mentions is dropped.
4. The uploader reviews and edits, then sends it to the approval queue (kind `employee_profile`, decided by an HR manager or admin).
5. A new person is created; for an existing person (same number, or the one person with that name) only missing skills and roles are added. Existing ratings are kept and nothing is removed.

### Standardization
Maps raw skill names to a canonical taxonomy (exact match, alias, fuzzy/semantic, model). Low-certainty mappings are listed for review.

### Skill matrix, employees, dashboard
Matrix of people × skills, employee profiles with seniority and level names, and a dashboard (mean level, most common skills, single points of failure at level 3+, level distribution, categories).

### Project planner
- Extracts required skills from a project description for the user to confirm.
- **Predicted gaps:** coverage, people one level short, best level in the company, risk, upskilling candidates. A short AI forecast narrative sits next to the numbers.
- **Suggested staffing:** team built skill-first, with a short AI "why this team" summary, additional capacity, and suggested learning for uncovered skills.
- **Candidates by position:** one position per preferred role. The top 5 candidates per position, each person under one position only (best fit first). Scores come from the ratings; anyone below a required level gets a **Learning path** button that opens the learning page with their gap skills and target levels already filled in.

### Learning paths and coach
- The model picks resources and writes a reason and a practice task per skill; **code verifies** each pick (right skill, right kind, not sample data when real data exists, course not longer than the planned hours, text without links or numbers).
- A second **reviewer** call checks the draft; what it changed is shown on the page.
- Hours, schedule, the skill-gap table and the hours breakdown are always computed by code. Failures fall back to a rule-based plan.
- Project managers, HR managers and admins can edit a plan; a check endpoint re-verifies the facts after edits.
- The **coach** answers questions about the plan and may give advice only from an `allowed_advice` list that code builds from the plan.

### Roles and role matching
Roles are free text in uploads, so the same job can appear as "Developer", "developer" and "Dev.". The **Roles** page lists every role in use with its spelling variants, and **Match with AI** (one employee, or everyone for an HR manager/admin) works in three steps:
1. **Code** finds spelling variants and proposes the most common spelling.
2. **The AI** compares each employee's roles with their skills and proposes `rename` (same job, different name), `add` (skills clearly support a role) or `remove` (skills do not support it).
3. **Code verifies**: an `add` must cite at least two of the employee's own skills at level 2 or higher and, when at least three other people hold that role, the employee must have 40% of the skills those people usually have; a `remove` is allowed only when at least three others hold the role, the employee has under 20% of its usual skills, and it is not their only role. All numbers shown are computed by code. Skills and levels are never changed by this.

Each employee gets one `employee_roles` proposal; the approver can untick single changes. HR manager, project manager and admin can edit any employee's roles by hand on the employee page (applied at once, logged). A team leader can edit, and run the AI match for, only their own members: the employees whose member account lists that team leader. New roles from members and CVs use the existing canonical spelling. Skill *names* are still matched by the Standardization step.

### Agents and approval queue
Agents: Standardization, Upload Check, Project Intake, Learning Coach, and Ask the matrix (chat). Agents may only **propose**. Proposal kinds and who decides them:

| Kind | Decided by |
| --- | --- |
| `skill_mapping` | HR manager |
| `upload_fix` | HR manager |
| `project_brief` | Project manager / team leader |
| `employee_profile` (CV) | HR manager |
| `employee_roles` (AI role match) | HR manager, or the team leader of that employee's member account (admin always) |
| `member_update`, `member_cv`, `member_learning_plan` | The requesting member's own team leader (admin always) |

Admin can decide anything. Only an exact match or an earlier human decision is applied without a person, and it is recorded as `auto_applied`.

---

## 4. Roles

`admin`, `project_manager`, `team_leader`, `hr_manager`, `member`. Admin always passes role checks. Learning plans can be edited by `project_manager`, `hr_manager` and `admin` (and by a member, for their own plan only).

### Members
A member can sign in but works on **one employee record only**, the one the admin linked to the account (`users.employee_id`). A member can:
- upload their own CV (PDF/Word), edit their own skill levels (0–4; 0 asks to remove a skill) and their profile (roles, domain expertise, background; name and employee number stay with HR),
- generate a learning path and edit it.

Nothing a member does changes the matrix directly. Each action becomes a proposal (`member_update`, `member_cv`, `member_learning_plan`) that waits for the member's **team leader** (`users.team_leader_id`, set by the admin or an HR manager). If no team leader is set, or that leader is disabled, only an admin can decide. A proposal is visible only to its member, that member's team leader and the admin. Approving a plan saves it in `learning_plans`.

Members are shut out of every other endpoint on the server (`deps.staff_user`); they only have `/api/me/*`, the learning-path routes for their own employee id, and their own proposals. The menu and page redirects are a convenience on top of that. The admin and HR manager manage members on the **Members & team leaders** page.

---

## 5. Seniority rule (code, not AI)

Computed on every read from the skill levels; no database column. Constants are at the top of `backend/app/services/seniority.py`.

| Condition | Label |
| --- | --- |
| Fewer than 3 rated skills | Not assessed |
| Average ≥ 3.0 and at least 3 skills at level 3–4 | Senior |
| Average < 1.75 | Junior |
| Everything else | Mid |

Only skills rated 1–4 count.

---

## 6. Where AI is used, and how it is checked

| Step | Model does | Code checks |
| --- | --- | --- |
| File parsing fallback | Extracts employees from messy text | Ratings normalised to 0–4, warnings for the rest |
| Standardization | Proposes a canonical skill | Confidence thresholds, review queue |
| Requirements | Extracts skills and levels | Levels clamped to 1–4, skills standardized |
| Gap risk | Estimates risk | Heuristic fallback, computed coverage |
| Learning plan | Picks resources, reason, practice | `verify_picks`, text rules, plan-fact check, fallback |
| Plan review | Corrects the draft | Same verification afterwards |
| CV | Estimates levels, quotes evidence | Quote found in CV, level cap 3, human approval |
| Staffing summary, gap narrative, seniority sentence | Word the facts | `services/narrative.py`: numbers, names and skills must come from the facts |
| Coach | Answers questions | Verified context, `allowed_advice` only |

Guardrails (`ai/guardrails.py`) check input for injection and output for cursing/toxicity.

---

## 7. Configuration

Secrets are **never hardcoded**. Provide them as environment variables or through your platform's secret store.

| Variable | Purpose | Default |
| --- | --- | --- |
| `OPENAI_API_KEY` | Enables all model features | unset (heuristic fallbacks) |
| `AI_NARRATIVES` | `false` turns off the staffing summary and gap narrative | on |
| `LEARNING_PLAN_REVIEW` | `false` turns off the plan-review call | on |
| `LEARNING_PLAN_MAX_TOKENS` | Token limit for plan calls | 2000 |

Other settings (upload folder, maximum upload size, orchestration mode, and so on) live in `backend/app/config.py`.

---

## 8. Project layout

```
backend/app/
  ai/         prompts, tasks (model calls), orchestration (learning-plan pipeline), guardrails,
              evaluation (golden set, RAGAS), eval_checks, chat_eval, predict, vectorstore
  agents/     agent prompts, tools, proposals (approval queue)
  services/   repo, projects, learning, seniority, narrative, standardization, groups, auth, roles, role_match
    parsing/  common, excel_parser, pdf_parser, cv_parser
  routers/    uploads, projects, data (employees, matrix, taxonomy), agents, auth, member (/api/me), roles
  schemas.py, db.py
frontend/
  app/        upload, employees, matrix, dashboard, projects, learning, approvals, standardize, me (member), members (admin/HR), ...
  components/ ui, RequirementsEditor, CvUpload, LearningPlanApproval, RunPanel, AppFrame
  lib/        api, types
golden/       people.json, golden_set.json (evaluation data, 0–4 scale)
```

---

## 9. Evaluation

A golden set (`golden/people.json`, `golden/golden_set.json`) covers standardization, staffing, learning, coach, chat and guardrail cases. Metrics: faithfulness, answer relevancy and context relevance (RAGAS 0.4.3, judge gpt-4.1-mini), plus deterministic checks (staffing coverage and contributions, chat facts, learning hours).

```
--golden --runs 3                        # full golden run
--golden --std-only --matchers llm,jev   # standardization only
```

`golden_check()` verifies the golden files are consistent with each other (no key needed).

---

## 10. Setup and running

## Run with Docker

```bash
cp .env.example .env          # set ADMIN_USERNAME, ADMIN_PASSWORD, AUTH_SECRET (needed to log in); OPENAI_API_KEY for AI/agents
docker compose up --build
```

UI: http://localhost:3000 · API docs: http://localhost:8000/docs

## Run locally

```bash
# backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env     # edit
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend && npm install && npm run dev

Before first use of the 0–4 scale, **reload your data as 0–4 files**; existing 1–5 ratings are not converted.

---
## Notes

- Simple built-in login (scrypt passwords, signed cookie, audit log), single tenant. Employee data is personal data: serve it over HTTPS (`AUTH_COOKIE_SECURE=true`) and behind your own network controls before real use.
- Uploads are proxied through Next.js (≈10 MB limit by default).
- Staffing score = `0.6 × skill fit + 0.3 × semantic profile similarity + 0.1 × role match` (env-tunable). It ranks people; it does not pick the team on its own.
- **Team building is complementary**: each person is added for required skills the team still lacks (a skill is covered once `min_people` members reach its level). Nobody is added as filler while a required skill is still missing. Only when everything is covered are leftover seats (up to the team size) filled with the best-scoring people, marked "extra capacity".
- **Skills nobody has**: no one is added for them. Instead the best-fit employee **outside the team** (closest current level + related strengths, same measure as "Best upskilling candidates") gets a study plan, shown automatically. One person per skill, `min_people` people if more are needed; skills given to the same person share one plan. Weeks and hours per week are set on the planner page.
- **Broad skills and tools**: projects ask for broad skills (Cloud, Cybersecurity, Microservices, Mobile Development, LLMs & Prompt Engineering) while people are rated on tools (AWS, Terraform, Splunk…). `backend/app/data/skill_groups.json` lists which tools count towards each broad skill; a person's rating for the broad skill is the best rating among those tools. Edit the file to add tools or new broad skills (restart the backend). The planner shows when a skill was counted through a tool.
- If someone has a skill but the team size is too small to include them, the page says so instead of suggesting a study plan.
- **Learning content** comes from three sources, in this order (`COURSE_SOURCES=catalog,mslearn,youtube`), mixed so each source shows up:
  1. **Your catalog**: `backend/app/data/course_catalog.csv` (columns `skill,title,url,provider,level,duration_hours,type,rating`; `type` = course, video or docs). Only rows you write are shown. A broad skill such as Cloud also gets the rows of its tools (AWS, Terraform…). The file ships with just a header row. Edit it and the next plan uses it, no restart needed.
  2. **Microsoft Learn** (free, no key): the public Catalog API. It has no search, so the catalog is downloaded and kept in `backend/data/cache` for `COURSE_CACHE_HOURS`, then matched by whole words in title, product, subject and summary. Microsoft technologies only.
  3. **YouTube** (needs `YOUTUBE_API_KEY`): one search and one length lookup per skill and level, cached for `YOUTUBE_CACHE_DAYS`, capped at `YOUTUBE_MAX_SEARCHES_PER_DAY` (default 90; YouTube's default allowance is about 100 searches a day). The key is read from the environment or your platform's secret store only and is sent in a header.
  A source that is down, unconfigured or out of quota is skipped with a warning; the plan is still made. Plans show a "Study" link and, when a video exists, a "Watch" link. Udemy is still available as an optional extra: add `udemy` to `COURSE_SOURCES` (`UDEMY_MODE=mock` gives sample links to a Udemy search page).

## Troubleshooting: backend "running" but port 8000 not responding

- Run uvicorn **from the `backend/` folder** (`uvicorn app.main:app --port 8000`), then check `curl http://localhost:8000/api/health` -> `{"ok":true}`.
- Startup no longer waits for the taxonomy vector index (it builds in the background; watch the log for `Taxonomy vector index ready` or an error).
- If it still hangs, run with `--log-level debug`, press Ctrl+C and read where the traceback stops. Usual causes: `OPENAI_API_KEY` set but the network is blocked, an unreachable `REDIS_URL`/`CHROMA_HOST`, or a very new Python version without wheels for `chromadb`/`onnxruntime` (use Python 3.12 or 3.13).
- Quick isolation: start with `OPENAI_API_KEY=` empty, `REDIS_URL` unset and `ANONYMIZED_TELEMETRY=False`.


## Toxic / cursing language filter

Two layers: an offline word list (`backend/app/data/blocked_terms.txt`, editable) and OpenAI's moderation model (harassment, hate; needs `OPENAI_API_KEY`). Input is **rejected** with a message asking to rephrase; AI output is **masked** (`f***`), or withheld when only the model flags it. English only. Settings: `GUARDRAILS_TOXICITY`, `GUARDRAILS_MODERATION_FAIL`, `MODERATION_MODEL` (see `.env.example`). Test prompts: `We need 3 fucking engineers who know Python` (word list) and `People like you should disappear, you worthless person` (model check).

## Evaluating "Ask the matrix"

- **Fixed question set (repeatable):** `python -m app.ai.chat_eval --list` shows the questions and expected answers (built from your current data: who has a skill, how many, one person's skills and role, a skill nobody has, things the data cannot answer such as salary). `python -m app.ai.chat_eval --run` asks them through the real chat agent (needs `OPENAI_API_KEY`) and also tests that an injection and a cursing question are rejected.
- **Real questions:** every question asked on the chat page is logged with its answer and the data the tools returned (names appear in the log: protect the file, or set `EVAL_LOG_ENABLED=false`). They have no reference answer, so they get the rule-based checks only; add a `ground_truth` to a row in `samples/eval_dataset.json` to include it in RAGAS.
- **Then:** `python -m app.ai.make_eval_dataset` and `python -m app.ai.evaluation --kind chat`. Rule-based checks are exact: right people listed, right counts, ratings quoted match, nobody named who the tools never returned, no invented salary.

## 11. Change log

**0–4 update**
- Skill scale changed from 1–5 to 0–4 across parsers, prompts, services, evaluation and the UI; golden data converted.
- Seniority (Junior/Mid/Senior) computed by code and shown in uploads, employee pages and chat.
- Single-CV upload (PDF/Word) with verified estimates and approval.
- Learning plan: AI plans, code verifies; reviewer step; editing by project manager, HR manager and admin.
- New AI texts: staffing summary, gap narrative, coach `allowed_advice`.
- Evaluation fixes: staffing cases are now scored; explicit `max_tokens` on model calls.

**Role matching**
- AI role match: spelling variants by code, role-versus-skill proposals by the AI, verified by code, approved by an HR manager or the employee's team leader.
- Manual role editing on the employee page; new `Roles` page; new column `agent_proposals.subject_employee_id` (added automatically).
- Only employees that have a member account have a team leader; for all others HR or admin decides.

**Member role**
- New `member` role linked to one employee; every change is approved by the member's team leader (admin as fallback).
- New tables/columns (added automatically at start-up): `users.employee_id`, `users.team_leader_id`, `uploads.uploaded_by`, `agent_proposals.requested_by`, `learning_plans`.
- Existing employee, matrix, upload, project and agent endpoints are closed to members on the server.